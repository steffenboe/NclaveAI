from __future__ import annotations

import copy
import json
import logging
import ssl
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.api_keys import ApiKeyRepository, generate_api_key, hash_api_key
from app.auth import (
    create_access_token,
    get_current_user,
    get_current_user_or_api_key,
    get_user_from_api_key,
    hash_password,
    require_admin,
    verify_password,
)
from app.config import settings
from app.executor import CommandExecutor
from app.models import ApiKeyCreated, ApiKeyPublic, Command, PolicyTestCase, PolicyTestResult, RunContext, ScheduledTask, Team, User, UserPublic
from app.planner import Planner
from app.policy import PolicyEvaluator
from app.policy_test import PolicyTestRepository, evaluate_policy_test
from app.runs import RunRepository, _match_hint, _run_matches
from app.scheduled_tasks import ScheduledTaskRepository
from app.secrets_store import SecretsStore
from app.settings_store import AppSettings, AppSettingsRepository
from app.skills import RemoteSkillRepository, SkillRepository
from app.teams import TeamRepository, get_team_assigned_skill_ids, resolve_team_llm, resolve_team_skills
from app.users import UserRepository
from app.workflow import AgentWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# In-memory stores
_runs: dict[str, RunContext] = {}
_runs_lock = threading.Lock()
_workflows: dict[str, AgentWorkflow] = {}
_workflows_lock = threading.Lock()

_scheduled_tasks: dict[str, ScheduledTask] = {}
_scheduled_tasks_lock = threading.Lock()
_scheduler_stop_event = threading.Event()
_scheduler_thread: threading.Thread | None = None

# Approval gate state
_approval_required: bool = False
_llm_base_url: str = settings.llm_base_url
_llm_api_key: str = settings.llm_api_key
_settings_lock = threading.Lock()

_pending_approvals: dict[str, "PendingApproval"] = {}
_pending_approvals_lock = threading.Lock()


@dataclass
class PendingApproval:
    run_id: str
    command: Command
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    actor_id: str | None = None   # set by approve/deny endpoint
    timed_out: bool = False       # set by gate on timeout


def _make_approval_gate(run_id: str, ctx: RunContext):
    def gate(command: Command) -> bool:
        approval = PendingApproval(run_id=run_id, command=command)
        with _pending_approvals_lock:
            _pending_approvals[run_id] = approval

        # Write pending_command BEFORE status so the UI never sees
        # waiting_approval without a command to display.
        ctx.pending_command = command.model_dump()
        ctx.status = "waiting_approval"

        timed_out = not approval.event.wait(timeout=300)
        ctx.last_actor_id = approval.actor_id
        
        if timed_out:
            with _pending_approvals_lock:
                _pending_approvals.pop(run_id, None)
            ctx.pending_command = None
            ctx._approval_expired = True
            approval.timed_out = True
            return False  # timed_out is authoritative; ignores any late approve
        else:
            ctx._approval_expired = False

        ctx.pending_command = None
        if approval.approved:
            ctx.status = "running"

        return approval.approved

    return gate


def _validate_timezone(tz_name: str) -> str:
    try:
        ZoneInfo(tz_name)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unknown timezone: {tz_name!r}") from exc
    return tz_name


def _compute_next_run_at(cron_expr: str, tz_name: str, now_utc: datetime | None = None) -> datetime:
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = ZoneInfo(tz_name)
    local_now = now_utc.astimezone(tz)
    try:
        next_local = croniter(cron_expr, local_now).get_next(datetime)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid cron expression: {cron_expr!r}") from exc
    if next_local.tzinfo is None:
        next_local = next_local.replace(tzinfo=tz)
    return next_local.astimezone(timezone.utc)


def _assert_task_access(task: ScheduledTask, current_user: User) -> None:
    if task.owner_id != current_user.user_id:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task.task_id!r} not found")


def _start_run_internal(
    *,
    prompt: str,
    app: FastAPI,
    current_user: User,
    llm_model: str | None = None,
    context_run_id: str | None = None,
) -> RunResponse:
    run_id = str(uuid.uuid4())

    seeded_history: list = []
    history_start_index = 0
    seeded_overrides: dict[str, bool] = {}
    parent_run_id: str | None = None
    seeded_model: str | None = llm_model
    seeded_conv_history: list[dict] = []
    history_snapshot: list = []
    if context_run_id is not None:
        with _runs_lock:
            parent_ctx = _runs.get(context_run_id)
            if parent_ctx is not None:
                if current_user.role != "admin" and parent_ctx.owner_id != current_user.user_id:
                    raise HTTPException(status_code=404, detail=f"Run {context_run_id!r} not found")
                history_snapshot = list(parent_ctx.history)
                seeded_overrides = dict(parent_ctx.skill_overrides)
                parent_run_id = context_run_id
                if seeded_model is None:
                    seeded_model = parent_ctx.llm_model
                # Build conversation history: inherit parent's prior turns, then append this turn
                inherited = list(parent_ctx.conversation_history)
                inherited.append({"role": "user", "content": parent_ctx.prompt})
                if parent_ctx.final_message:
                    inherited.append({"role": "assistant", "content": parent_ctx.final_message})
                seeded_conv_history = inherited
        if parent_run_id is not None:
            seeded_history = copy.deepcopy(history_snapshot)
            history_start_index = len(seeded_history)

    ctx = RunContext(
        run_id=run_id,
        prompt=prompt,
        history=seeded_history,
        history_start_index=history_start_index,
        skill_overrides=seeded_overrides,
        parent_run_id=parent_run_id,
        llm_model=seeded_model,
        owner_id=current_user.user_id,
        conversation_history=seeded_conv_history,
        created_at=datetime.now(timezone.utc),
    )
    skill_repo = app.state.skill_repo
    remote_skill_repo = getattr(app.state, "remote_skill_repo", None)
    run_repo = app.state.run_repo

    app_settings_repo = getattr(app.state, "app_settings_repo", None)
    app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()
    if seeded_model is None:
        seeded_model = app_settings.default_model or settings.llm_model
    ctx.llm_model = seeded_model
    user_system_prompt = app_settings.system_prompt

    with _runs_lock:
        _runs[run_id] = ctx
    run_repo.save(ctx)

    def _execute() -> None:
        workflow = _build_workflow(
            skill_repo,
            run_id=run_id,
            ctx=ctx,
            remote_skill_repo=remote_skill_repo,
            secrets_store=app.state.secrets_store,
            user_require_approval=current_user.require_approval,
            audit_repo=getattr(app.state, "audit_repo", None),
            team_repo=getattr(app.state, "team_repo", None),
            user_id=current_user.user_id,
            team_remote_repos=getattr(app.state, "team_remote_repos", {}),
            user_system_prompt=user_system_prompt,
        )
        with _workflows_lock:
            _workflows[run_id] = workflow
        try:
            result = workflow.run(
                prompt=prompt,
                max_iterations=settings.max_iterations,
                ctx=ctx,
            )
            with _runs_lock:
                _runs[run_id] = result
            run_repo.save(result)
        finally:
            with _workflows_lock:
                _workflows.pop(run_id, None)

    thread = threading.Thread(target=_execute, daemon=True)
    thread.start()

    return RunResponse(run_id=run_id, status="running")


def _scheduler_loop(app: FastAPI) -> None:
    while not _scheduler_stop_event.wait(timeout=1):
        now = datetime.now(timezone.utc)
        due_tasks: list[ScheduledTask] = []
        with _scheduled_tasks_lock:
            for task in _scheduled_tasks.values():
                if not task.enabled or task.next_run_at is None:
                    continue
                if task.next_run_at <= now:
                    due_tasks.append(task.model_copy())

        for task in due_tasks:
            try:
                user_repo = app.state.user_repo
                owner = user_repo.get(task.owner_id)
                if owner is None:
                    raise RuntimeError(f"owner {task.owner_id!r} not found")

                run_response = _start_run_internal(
                    prompt=task.prompt,
                    app=app,
                    current_user=owner,
                )

                with _scheduled_tasks_lock:
                    live = _scheduled_tasks.get(task.task_id)
                    if live is None:
                        continue
                    live.last_run_at = now
                    live.last_run_id = run_response.run_id
                    live.last_error = None
                    live.next_run_at = _compute_next_run_at(live.cron, live.timezone, now)
                    live.updated_at = now
                    app.state.scheduled_task_repo.save(live)
            except Exception as exc:
                with _scheduled_tasks_lock:
                    live = _scheduled_tasks.get(task.task_id)
                    if live is None:
                        continue
                    live.last_error = str(exc)
                    try:
                        live.next_run_at = _compute_next_run_at(live.cron, live.timezone, now)
                    except Exception:
                        live.next_run_at = None
                    live.updated_at = now
                    app.state.scheduled_task_repo.save(live)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- persistence backend selection ---
    if settings.mongodb_uri:
        from pymongo import MongoClient

        from app.mongo_repos import (
            MongoAppSettingsRepository,
            MongoRunRepository,
            MongoScheduledTaskRepository,
            MongoSkillRepository,
            MongoUserRepository,
        )

        _mongo_client = MongoClient(settings.mongodb_uri)
        mongo_db = _mongo_client[settings.mongodb_db_name]
        from urllib.parse import urlparse, urlunparse
        _parsed = urlparse(settings.mongodb_uri)
        _host = (_parsed.hostname or "") + (f":{_parsed.port}" if _parsed.port else "")
        _safe_uri = urlunparse(_parsed._replace(netloc=_host))
        logger.info("Using MongoDB backend: %s / %s", _safe_uri, settings.mongodb_db_name)

        skill_repo = MongoSkillRepository(mongo_db)
        app_settings_repo = MongoAppSettingsRepository(mongo_db)
        run_repo = MongoRunRepository(mongo_db)
        scheduled_task_repo = MongoScheduledTaskRepository(mongo_db)
        user_repo = MongoUserRepository(mongo_db)
        from app.mongo_repos import MongoApiKeyRepository, MongoTeamRepository
        team_repo: TeamRepository = MongoTeamRepository(mongo_db)
        api_key_repo: ApiKeyRepository = MongoApiKeyRepository(mongo_db)
        from app.mongo_repos import MongoPolicyTestRepository
        policy_test_repo: PolicyTestRepository = MongoPolicyTestRepository(mongo_db)
    else:
        skill_repo = SkillRepository(settings.skills_file)
        app_settings_repo = AppSettingsRepository(settings.settings_file)
        run_repo = RunRepository(settings.runs_file)
        scheduled_task_repo = ScheduledTaskRepository(settings.scheduled_tasks_file)
        user_repo = UserRepository(settings.users_file)
        team_repo = TeamRepository(settings.teams_file)
        api_key_repo: ApiKeyRepository = ApiKeyRepository(settings.api_keys_file)
        policy_test_repo: PolicyTestRepository = PolicyTestRepository(settings.policy_test_file)
    # --- audit repository selection ---
    if settings.mongodb_uri:
        from app.audit import MongoAuditRepository
        audit_repo = MongoAuditRepository(mongo_db)
    else:
        from app.audit import FileAuditRepository
        audit_repo = FileAuditRepository(settings.audit_file)
    
    app.state.audit_repo = audit_repo

    app.state.skill_repo = skill_repo
    app.state.app_settings_repo = app_settings_repo
    app_settings = app_settings_repo.load()

    # Restore persisted LLM settings into in-memory state
    with _settings_lock:
        global _llm_base_url, _llm_api_key
        if app_settings.llm_base_url:
            _llm_base_url = app_settings.llm_base_url
        if app_settings.llm_api_key:
            _llm_api_key = app_settings.llm_api_key
    app.state.remote_skill_repo = None
    if app_settings.skills_repo_url:
        import asyncio
        remote_repo = RemoteSkillRepository(
            app_settings.skills_repo_url,
            branch=app_settings.skills_repo_branch,
        )
        try:
            await asyncio.to_thread(remote_repo.sync)
            app.state.remote_skill_repo = remote_repo
            logger.info("Remote skills loaded from %s", app_settings.skills_repo_url)
        except Exception as exc:
            logger.warning("Failed to load remote skills: %s", exc)

    # Sync per-team remote skill repositories once at startup
    import asyncio as _asyncio
    app.state.team_repo = team_repo
    team_remote_repos: dict[str, RemoteSkillRepository] = {}
    for _team in team_repo.list():
        _url = _team.skill_repo_url
        if not _url or _url in team_remote_repos:
            continue
        _repo = RemoteSkillRepository(_url, branch=_team.skill_repo_branch)
        try:
            await _asyncio.to_thread(_repo.sync)
            team_remote_repos[_url] = _repo
            logger.info("Team remote skills loaded from %s", _url)
        except Exception as exc:
            logger.warning("Failed to load team remote skills from %s: %s", _url, exc)
    app.state.team_remote_repos = team_remote_repos

    app.state.run_repo = run_repo
    app.state.scheduled_task_repo = scheduled_task_repo
    app.state.secrets_store = SecretsStore(settings.secrets_file)
    with _runs_lock:
        _runs.update(run_repo.all_as_dict())
    with _scheduled_tasks_lock:
        _scheduled_tasks.clear()
        _scheduled_tasks.update(scheduled_task_repo.all_as_dict())
        now = datetime.now(timezone.utc)
        for task in _scheduled_tasks.values():
            if not task.enabled:
                task.next_run_at = None
                continue
            try:
                task.next_run_at = _compute_next_run_at(task.cron, task.timezone, now)
            except HTTPException as exc:
                logger.warning("Could not schedule task %s: %s", task.task_id, exc.detail)
                task.next_run_at = None
                task.last_error = str(exc.detail)
            scheduled_task_repo.save(task)

    global _scheduler_thread
    _scheduler_stop_event.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, args=(app,), daemon=True)
    _scheduler_thread.start()

    app.state.user_repo = user_repo
    app.state.api_key_repo = api_key_repo
    app.state.policy_test_repo = policy_test_repo
    if user_repo.count() == 0 and settings.admin_password:
        user_repo.create(
            username=settings.admin_username,
            hashed_password=hash_password(settings.admin_password),
            role="admin",
        )
        logger.info("Bootstrapped admin user %r", settings.admin_username)

    yield

    _scheduler_stop_event.set()
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=2)


app = FastAPI(title="notesllm-agent", version="0.1.0", lifespan=lifespan)


def _all_skills(request: Request) -> list:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    remote = remote_repo.list_skills() if remote_repo else []
    local = request.app.state.skill_repo.list()
    return remote + local


def _skills_for_user(request: Request, current_user: User) -> list:
    """Return the skills visible to the given user, applying team filtering.

    Admins always receive the full unfiltered set (for management UIs).
    Regular users receive the same filtered set as _build_workflow produces:
      - team member  → union of their teams' skill_ids + team remote repos
      - no team      → global skills, excluding any skill assigned to a team
    """
    if current_user.role == "admin":
        return _all_skills(request)

    skill_repo: SkillRepository = request.app.state.skill_repo
    team_repo = getattr(request.app.state, "team_repo", None)
    team_remote_repos: dict = getattr(request.app.state, "team_remote_repos", {})
    remote_skill_repo = getattr(request.app.state, "remote_skill_repo", None)

    if team_repo is None:
        return _all_skills(request)

    team_context = resolve_team_skills(
        current_user.user_id, team_repo, skill_repo, team_remote_repos
    )
    if team_context is not None:
        local_skills, remote_skills = team_context
    else:
        team_assigned = get_team_assigned_skill_ids(team_repo)
        local_skills = [s for s in skill_repo.list() if s.id not in team_assigned]
        remote_skills = remote_skill_repo.list_skills() if remote_skill_repo else []

    return remote_skills + local_skills


def _build_workflow(
    skill_repo: SkillRepository,
    run_id: str | None = None,
    ctx: RunContext | None = None,
    remote_skill_repo=None,
    secrets_store: SecretsStore | None = None,
    user_require_approval: bool = False,
    audit_repo=None,
    team_repo: TeamRepository | None = None,
    user_id: str | None = None,
    team_remote_repos: dict | None = None,
    user_system_prompt: str | None = None,
) -> AgentWorkflow:
    local_skills = skill_repo.list()
    remote_skills = remote_skill_repo.list_skills() if remote_skill_repo else []
    gate = None
    llm_base_url = settings.llm_base_url
    llm_api_key = settings.llm_api_key
    llm_model = settings.llm_model
    if run_id is not None and ctx is not None:
        with _settings_lock:
            need_approval = _approval_required or user_require_approval
            llm_base_url = _llm_base_url
            llm_api_key = _llm_api_key
        if need_approval:
            gate = _make_approval_gate(run_id, ctx)
        # Use model from context if set, otherwise use default
        if ctx.llm_model is not None:
            llm_model = ctx.llm_model

    # Apply team-scoped skill and LLM filtering
    if team_repo is not None and user_id is not None:
        team_context = resolve_team_skills(
            user_id, team_repo, skill_repo, team_remote_repos or {}
        )
        if team_context is not None:
            # User has team membership → skills are the union of their teams
            local_skills, remote_skills = team_context
        else:
            # No team membership → global skills, but exclude any skill already
            # assigned to a team (those are team-private)
            team_assigned = get_team_assigned_skill_ids(team_repo)
            local_skills = [s for s in local_skills if s.id not in team_assigned]
        llm_base_url, llm_api_key = resolve_team_llm(
            user_id, team_repo, llm_base_url, llm_api_key
        )

    all_skills = remote_skills + local_skills
    return AgentWorkflow(
        planner=Planner(
            skill_repo,
            local_skills=local_skills,
            remote_skills=remote_skills,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            user_system_prompt=user_system_prompt,
        ),
        policy=PolicyEvaluator(skills=all_skills),
        executor=CommandExecutor(),
        approval_gate=gate,
        secrets_store=secrets_store,
        audit_repo=audit_repo,
    )


# ── Auth ─────────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: Literal["admin", "user"] = "user"


class UserPatchRequest(BaseModel):
    username: str | None = None
    role: Literal["admin", "user"] | None = None
    require_approval: bool | None = None


@app.post("/api/auth/login")
def login(body: LoginRequest, response: Response, request: Request) -> UserPublic:
    user_repo: UserRepository = request.app.state.user_repo
    user = user_repo.get_by_username(body.username)
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(subject=user.user_id, role=user.role)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=8 * 60 * 60,
    )
    return UserPublic(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        created_at=user.created_at,
        require_approval=user.require_approval,
    )


@app.post("/api/auth/logout")
def logout(response: Response, current_user: User = Depends(get_current_user)) -> dict:
    response.delete_cookie("access_token")
    return {"status": "logged out"}


@app.get("/api/auth/me", response_model=UserPublic)
def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic(
        user_id=current_user.user_id,
        username=current_user.username,
        role=current_user.role,
        created_at=current_user.created_at,
        require_approval=current_user.require_approval,
    )


@app.post("/api/auth/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user_repo: UserRepository = request.app.state.user_repo
    user_repo.update(current_user.user_id, hashed_password=hash_password(body.new_password))
    return {"status": "password changed"}


# ── Users ───────────────────────────────────────────────────────────────────


@app.get("/api/users", response_model=list[UserPublic])
def list_users(request: Request, current_user: User = Depends(require_admin)) -> list[UserPublic]:
    user_repo: UserRepository = request.app.state.user_repo
    return [
        UserPublic(user_id=u.user_id, username=u.username, role=u.role, created_at=u.created_at, require_approval=u.require_approval)
        for u in user_repo.list()
    ]


@app.post("/api/users", status_code=201, response_model=UserPublic)
def create_user(
    body: UserCreateRequest,
    request: Request,
    current_user: User = Depends(require_admin),
) -> UserPublic:
    user_repo: UserRepository = request.app.state.user_repo
    if user_repo.get_by_username(body.username) is not None:
        raise HTTPException(status_code=409, detail=f"Username {body.username!r} already taken")
    user = user_repo.create(
        username=body.username,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    return UserPublic(user_id=user.user_id, username=user.username, role=user.role, created_at=user.created_at, require_approval=user.require_approval)


@app.patch("/api/users/{user_id}", response_model=UserPublic)
def patch_user(
    user_id: str,
    body: UserPatchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> UserPublic:
    user_repo: UserRepository = request.app.state.user_repo
    user = user_repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found")
    if current_user.role != "admin" and current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's account")
    if body.role is not None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Cannot change your own role")
    kwargs: dict[str, object] = {}
    if body.username is not None:
        kwargs["username"] = body.username
    if body.role is not None:
        kwargs["role"] = body.role
    if body.require_approval is not None:
        kwargs["require_approval"] = body.require_approval
    if not kwargs:
        return UserPublic(user_id=user.user_id, username=user.username, role=user.role, created_at=user.created_at, require_approval=user.require_approval)
    updated = user_repo.update(user_id, **kwargs)
    return UserPublic(user_id=updated.user_id, username=updated.username, role=updated.role, created_at=updated.created_at, require_approval=updated.require_approval)


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
) -> None:
    if current_user.user_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user_repo: UserRepository = request.app.state.user_repo
    try:
        user_repo.delete(user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found")


# ── Teams ─────────────────────────────────────────────────────────────────────


class TeamCreateRequest(BaseModel):
    name: str
    skill_ids: list[str] = []
    skill_repo_url: str | None = None
    skill_repo_branch: str = "main"
    llm_base_url: str | None = None
    llm_api_key: str | None = None


class TeamUpdateRequest(BaseModel):
    name: str | None = None
    skill_ids: list[str] | None = None
    skill_repo_url: str | None = None
    skill_repo_branch: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None


class TeamResponse(BaseModel):
    team_id: str
    name: str
    user_ids: list[str]
    skill_ids: list[str]
    skill_repo_url: str | None
    skill_repo_branch: str
    llm_base_url: str | None
    has_llm_api_key: bool
    created_at: datetime
    updated_at: datetime


def _team_response(team: Team) -> TeamResponse:
    return TeamResponse(
        team_id=team.team_id,
        name=team.name,
        user_ids=team.user_ids,
        skill_ids=team.skill_ids,
        skill_repo_url=team.skill_repo_url,
        skill_repo_branch=team.skill_repo_branch,
        llm_base_url=team.llm_base_url,
        has_llm_api_key=bool(team.llm_api_key),
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@app.get("/api/teams", response_model=list[TeamResponse])
def list_teams(
    request: Request,
    current_user: User = Depends(require_admin),
) -> list[TeamResponse]:
    team_repo: TeamRepository = request.app.state.team_repo
    return [_team_response(t) for t in team_repo.list()]


@app.post("/api/teams", status_code=201, response_model=TeamResponse)
def create_team(
    body: TeamCreateRequest,
    request: Request,
    current_user: User = Depends(require_admin),
) -> TeamResponse:
    team_repo: TeamRepository = request.app.state.team_repo
    try:
        team = team_repo.create(
            name=body.name,
            skill_ids=body.skill_ids,
            skill_repo_url=body.skill_repo_url,
            skill_repo_branch=body.skill_repo_branch,
            llm_base_url=body.llm_base_url,
            llm_api_key=body.llm_api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _team_response(team)


@app.get("/api/teams/{team_id}", response_model=TeamResponse)
def get_team(
    team_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
) -> TeamResponse:
    team_repo: TeamRepository = request.app.state.team_repo
    team = team_repo.get(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail=f"Team {team_id!r} not found")
    return _team_response(team)


@app.put("/api/teams/{team_id}", response_model=TeamResponse)
def update_team(
    team_id: str,
    body: TeamUpdateRequest,
    request: Request,
    current_user: User = Depends(require_admin),
) -> TeamResponse:
    team_repo: TeamRepository = request.app.state.team_repo
    if team_repo.get(team_id) is None:
        raise HTTPException(status_code=404, detail=f"Team {team_id!r} not found")
    kwargs: dict[str, object] = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.skill_ids is not None:
        kwargs["skill_ids"] = body.skill_ids
    if "skill_repo_url" in body.model_fields_set:
        kwargs["skill_repo_url"] = body.skill_repo_url
    if body.skill_repo_branch is not None:
        kwargs["skill_repo_branch"] = body.skill_repo_branch
    if "llm_base_url" in body.model_fields_set:
        kwargs["llm_base_url"] = body.llm_base_url
    if "llm_api_key" in body.model_fields_set:
        kwargs["llm_api_key"] = body.llm_api_key
    try:
        updated = team_repo.update(team_id, **kwargs)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id!r} not found")
    return _team_response(updated)


@app.delete("/api/teams/{team_id}", status_code=204)
def delete_team(
    team_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
) -> None:
    team_repo: TeamRepository = request.app.state.team_repo
    try:
        team_repo.delete(team_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id!r} not found")


@app.post("/api/teams/{team_id}/members/{user_id}", response_model=TeamResponse)
def add_team_member(
    team_id: str,
    user_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
) -> TeamResponse:
    team_repo: TeamRepository = request.app.state.team_repo
    user_repo: UserRepository = request.app.state.user_repo
    if user_repo.get(user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found")
    try:
        team = team_repo.add_member(team_id, user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id!r} not found")
    return _team_response(team)


@app.delete("/api/teams/{team_id}/members/{user_id}", response_model=TeamResponse)
def remove_team_member(
    team_id: str,
    user_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
) -> TeamResponse:
    team_repo: TeamRepository = request.app.state.team_repo
    user_repo: UserRepository = request.app.state.user_repo
    if user_repo.get(user_id) is None:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found")
    try:
        team = team_repo.remove_member(team_id, user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id!r} not found")
    return _team_response(team)


# ── Manual run ────────────────────────────────────────────────────────────────


def _assert_run_access(ctx: RunContext, current_user: User) -> None:
    """Raise HTTP 404 if the current user doesn't own this run. Admins bypass."""
    if current_user.role == "admin":
        return
    if ctx.owner_id != current_user.user_id:
        raise HTTPException(status_code=404, detail=f"Run {ctx.run_id!r} not found")


class RunRequest(BaseModel):
    prompt: str
    context_run_id: str | None = None
    llm_model: str | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    enabled: bool = True
    policy: str | None = None
    env: list[str] | None = None


class SkillPatchRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    policy: str | None = None
    env: list[str] | None = None


class GeneratePolicyRequest(BaseModel):
    skill_name: str
    skill_description: str
    description: str


@app.post("/api/agent/run", status_code=202, response_model=RunResponse)
def start_run(request: RunRequest, req: Request, current_user: User = Depends(get_current_user)) -> RunResponse:
    return _start_run_internal(
        prompt=request.prompt,
        app=req.app,
        current_user=current_user,
        llm_model=request.llm_model,
        context_run_id=request.context_run_id,
    )


class SearchHit(BaseModel):
    run_id: str
    root_run_id: str
    prompt: str
    status: str
    matched_in: str


def _resolve_root_run_id(run_id: str) -> str:
    """Walk parent_run_id chain to find the root run."""
    visited: set[str] = set()
    current = run_id
    while True:
        if current in visited:
            return current
        visited.add(current)
        with _runs_lock:
            ctx = _runs.get(current)
        if ctx is None or ctx.parent_run_id is None:
            return current
        current = ctx.parent_run_id


@app.get("/api/agent/runs/search", response_model=list[SearchHit])
def search_runs(q: str = "", current_user: User = Depends(get_current_user)) -> list[SearchHit]:
    """Full-text search across all runs (prompt, summary, command history)."""
    if not q.strip():
        return []
    query = q.lower()
    with _runs_lock:
        runs_snapshot = list(_runs.values())
    hits: list[SearchHit] = []
    for ctx in runs_snapshot:
        if ctx.owner_id != current_user.user_id:
            continue
        if _run_matches(ctx, query):
            hits.append(SearchHit(
                run_id=ctx.run_id,
                root_run_id=_resolve_root_run_id(ctx.run_id),
                prompt=ctx.prompt,
                status=ctx.status,
                matched_in=_match_hint(ctx, query),
            ))
    return hits


@app.get("/api/agent/runs/{run_id}")
def get_run(run_id: str, current_user: User = Depends(get_current_user)) -> Any:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    _assert_run_access(ctx, current_user)
    return ctx.model_dump()


@app.get("/api/agent/runs")
def list_runs(current_user: User = Depends(get_current_user)) -> list[dict]:
    with _runs_lock:
        runs = list(_runs.values())
    runs = [ctx for ctx in runs if ctx.owner_id == current_user.user_id]
    return [ctx.model_dump() for ctx in runs]


def _collect_descendants(root_run_id: str) -> set[str]:
    """Collect all descendants for a run (including root) by parent_run_id."""
    to_visit = [root_run_id]
    collected: set[str] = set()
    while to_visit:
        current = to_visit.pop()
        if current in collected:
            continue
        collected.add(current)
        with _runs_lock:
            children = [
                run_id for run_id, ctx in _runs.items() if ctx.parent_run_id == current
            ]
        to_visit.extend(children)
    return collected


@app.delete("/api/agent/runs/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request, current_user: User = Depends(get_current_user)) -> None:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    _assert_run_access(ctx, current_user)

    to_delete = _collect_descendants(run_id)

    with _runs_lock:
        for rid in to_delete:
            _runs.pop(rid, None)

    for rid in to_delete:
        try:
            request.app.state.run_repo.delete(rid)
        except KeyError:
            # Repo can be out of sync in tests/startup edge cases.
            continue


# ── Scheduled tasks ──────────────────────────────────────────────────────────


class ScheduledTaskCreateRequest(BaseModel):
    prompt: str
    cron: str
    timezone: str = "UTC"
    enabled: bool = True


class ScheduledTaskPatchRequest(BaseModel):
    prompt: str | None = None
    cron: str | None = None
    timezone: str | None = None
    enabled: bool | None = None


class ScheduledTaskResponse(BaseModel):
    task_id: str
    owner_id: str
    prompt: str
    cron: str
    timezone: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_id: str | None = None
    last_error: str | None = None


def _scheduled_task_response(task: ScheduledTask) -> ScheduledTaskResponse:
    return ScheduledTaskResponse(**task.model_dump())


@app.get("/api/scheduled-tasks", response_model=list[ScheduledTaskResponse])
def list_scheduled_tasks(request: Request, current_user: User = Depends(get_current_user)) -> list[ScheduledTaskResponse]:
    with _scheduled_tasks_lock:
        tasks = list(_scheduled_tasks.values())
    tasks = [task for task in tasks if task.owner_id == current_user.user_id]
    return [_scheduled_task_response(task) for task in tasks]


@app.post("/api/scheduled-tasks", status_code=201, response_model=ScheduledTaskResponse)
def create_scheduled_task(
    body: ScheduledTaskCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> ScheduledTaskResponse:
    tz_name = _validate_timezone(body.timezone)
    next_run_at = _compute_next_run_at(body.cron, tz_name)
    now = datetime.now(timezone.utc)

    task = ScheduledTask(
        task_id=str(uuid.uuid4()),
        owner_id=current_user.user_id,
        prompt=body.prompt,
        cron=body.cron,
        timezone=tz_name,
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
        next_run_at=next_run_at if body.enabled else None,
    )

    with _scheduled_tasks_lock:
        _scheduled_tasks[task.task_id] = task
    request.app.state.scheduled_task_repo.save(task)
    return _scheduled_task_response(task)


@app.get("/api/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
def get_scheduled_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
) -> ScheduledTaskResponse:
    with _scheduled_tasks_lock:
        task = _scheduled_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task_id!r} not found")
    _assert_task_access(task, current_user)
    return _scheduled_task_response(task)


@app.patch("/api/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
def patch_scheduled_task(
    task_id: str,
    body: ScheduledTaskPatchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> ScheduledTaskResponse:
    with _scheduled_tasks_lock:
        task = _scheduled_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task_id!r} not found")
    _assert_task_access(task, current_user)

    now = datetime.now(timezone.utc)
    prompt = task.prompt if body.prompt is None else body.prompt
    cron_expr = task.cron if body.cron is None else body.cron
    tz_name = task.timezone if body.timezone is None else _validate_timezone(body.timezone)
    enabled = task.enabled if body.enabled is None else body.enabled

    next_run_at = _compute_next_run_at(cron_expr, tz_name, now) if enabled else None

    updated = task.model_copy(
        update={
            "prompt": prompt,
            "cron": cron_expr,
            "timezone": tz_name,
            "enabled": enabled,
            "next_run_at": next_run_at,
            "updated_at": now,
            "last_error": None,
        }
    )

    with _scheduled_tasks_lock:
        _scheduled_tasks[task_id] = updated
    request.app.state.scheduled_task_repo.save(updated)
    return _scheduled_task_response(updated)


@app.delete("/api/scheduled-tasks/{task_id}", status_code=204)
def delete_scheduled_task(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    with _scheduled_tasks_lock:
        task = _scheduled_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task_id!r} not found")
    _assert_task_access(task, current_user)

    with _scheduled_tasks_lock:
        _scheduled_tasks.pop(task_id, None)
    try:
        request.app.state.scheduled_task_repo.delete(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task_id!r} not found")


@app.post("/api/scheduled-tasks/{task_id}/run", status_code=202, response_model=RunResponse)
def run_scheduled_task_now(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    with _scheduled_tasks_lock:
        task = _scheduled_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Scheduled task {task_id!r} not found")
    _assert_task_access(task, current_user)

    user_repo = request.app.state.user_repo
    owner = user_repo.get(task.owner_id)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"Owner {task.owner_id!r} not found")

    run_response = _start_run_internal(
        prompt=task.prompt,
        app=request.app,
        current_user=owner,
    )

    now = datetime.now(timezone.utc)
    with _scheduled_tasks_lock:
        live = _scheduled_tasks.get(task_id)
        if live is not None:
            live.last_run_at = now
            live.last_run_id = run_response.run_id
            live.last_error = None
            live.updated_at = now
            if live.enabled:
                try:
                    live.next_run_at = _compute_next_run_at(live.cron, live.timezone, now)
                except HTTPException as exc:
                    live.next_run_at = None
                    live.last_error = str(exc.detail)
            request.app.state.scheduled_task_repo.save(live)

    return run_response


# ── Models ────────────────────────────────────────────────────────────────────


class ModelsResponse(BaseModel):
    available_models: list[str]
    default_model: str


@app.get("/api/models", response_model=ModelsResponse)
def get_models(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> ModelsResponse:
    app_settings_repo = getattr(request.app.state, "app_settings_repo", None)
    app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()
    default_model = app_settings.default_model or settings.llm_model

    with _settings_lock:
        base_url = _llm_base_url
        api_key = _llm_api_key

    # Normalise: strip any trailing /v1 so we always call <root>/v1/models
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = base + "/v1/models"

    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    ssl_ctx = ssl.create_default_context()
    if settings.llm_ca_bundle:
        ssl_ctx.load_verify_locations(cafile=settings.llm_ca_bundle)
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"API returned {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach LLM API: {exc}") from exc

    model_rows: list[dict] = []
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            model_rows = [m for m in data.get("data", []) if isinstance(m, dict)]
        elif isinstance(data.get("models"), list):
            model_rows = [m for m in data.get("models", []) if isinstance(m, dict)]
    elif isinstance(data, list):
        model_rows = [m for m in data if isinstance(m, dict)]

    available_models = sorted({
        str(m.get("id") or m.get("model") or "")
        for m in model_rows
        if (m.get("id") or m.get("model"))
    })

    return ModelsResponse(
        available_models=available_models,
        default_model=default_model,
    )


# ── Settings ──────────────────────────────────────────────────────────────────


class SettingsResponse(BaseModel):
    approval_required: bool
    llm_base_url: str
    has_llm_api_key: bool
    skills_repo_configured: bool
    skills_repo_url: str | None
    skills_repo_branch: str
    default_model: str | None
    system_prompt: str | None


class SettingsPatchRequest(BaseModel):
    approval_required: bool | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    skills_repo_url: str | None = None
    skills_repo_branch: str | None = None
    default_model: str | None = None
    system_prompt: str | None = None


@app.get("/api/settings", response_model=SettingsResponse)
def get_settings(request: Request, current_user: User = Depends(require_admin)) -> SettingsResponse:
    app_settings_repo = getattr(request.app.state, "app_settings_repo", None)
    app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()
    with _settings_lock:
        return SettingsResponse(
            approval_required=_approval_required,
            llm_base_url=_llm_base_url,
            has_llm_api_key=bool(_llm_api_key),
            skills_repo_configured=getattr(request.app.state, "remote_skill_repo", None)
            is not None,
            skills_repo_url=app_settings.skills_repo_url,
            skills_repo_branch=app_settings.skills_repo_branch,
            default_model=app_settings.default_model
            if app_settings.default_model
            else settings.llm_model,
            system_prompt=app_settings.system_prompt,
        )


@app.get("/api/settings/approval")
def get_approval_status(current_user: User = Depends(get_current_user)) -> dict:
    """Returns the global approval_required flag. Accessible to all authenticated users."""
    with _settings_lock:
        return {"approval_required": _approval_required}


@app.put("/api/settings", response_model=SettingsResponse)
def put_settings(body: SettingsPatchRequest, request: Request, current_user: User = Depends(require_admin)) -> SettingsResponse:
    global _approval_required, _llm_base_url, _llm_api_key

    # ── LLM settings (in-memory + persisted) ─────────────────────────────────
    with _settings_lock:
        if body.approval_required is not None:
            _approval_required = body.approval_required

        if body.llm_base_url is not None:
            trimmed_url = body.llm_base_url.strip()
            if not trimmed_url:
                raise HTTPException(
                    status_code=422, detail="llm_base_url must not be empty"
                )
            _llm_base_url = trimmed_url

        if body.llm_api_key is not None:
            _llm_api_key = body.llm_api_key.strip()

    # ── Repo settings (persisted) ─────────────────────────────────────────────
    app_settings_repo: AppSettingsRepository | None = getattr(
        request.app.state, "app_settings_repo", None
    )
    if app_settings_repo is None:
        app_settings_repo = AppSettingsRepository(settings.settings_file)
        request.app.state.app_settings_repo = app_settings_repo
    app_settings = app_settings_repo.load()
    settings_changed = False

    # ── LLM URL + key (persisted) ─────────────────────────────────────────────
    if body.llm_base_url is not None:
        app_settings.llm_base_url = body.llm_base_url.strip() or None
        settings_changed = True

    if body.llm_api_key is not None:
        app_settings.llm_api_key = body.llm_api_key.strip() or None
        settings_changed = True

    # ── Repo settings (persisted) ─────────────────────────────────────────────
    if "skills_repo_url" in body.model_fields_set:
        new_url = body.skills_repo_url.strip() if body.skills_repo_url else None
        new_branch = (
            body.skills_repo_branch.strip()
            if body.skills_repo_branch
            else app_settings.skills_repo_branch
        )
        app_settings.skills_repo_url = new_url
        app_settings.skills_repo_branch = new_branch
        settings_changed = True
        app_settings_repo.save(app_settings)
        settings_changed = False  # already saved

        if new_url:
            remote_repo = RemoteSkillRepository(new_url, branch=new_branch)
            try:
                remote_repo.sync()
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            request.app.state.remote_skill_repo = remote_repo
        else:
            request.app.state.remote_skill_repo = None

    # ── Model settings (persisted) ────────────────────────────────────────────
    if "default_model" in body.model_fields_set:
        if body.default_model is None:
            app_settings.default_model = None
        else:
            trimmed_default_model = body.default_model.strip()
            if not trimmed_default_model:
                raise HTTPException(
                    status_code=422, detail="default_model must not be empty"
                )
            app_settings.default_model = trimmed_default_model
        settings_changed = True

    # ── System prompt (persisted) ─────────────────────────────────────────────
    if "system_prompt" in body.model_fields_set:
        app_settings.system_prompt = body.system_prompt or None
        settings_changed = True

    if settings_changed:
        app_settings_repo.save(app_settings)

    app_settings = app_settings_repo.load()

    with _settings_lock:
        return SettingsResponse(
            approval_required=_approval_required,
            llm_base_url=_llm_base_url,
            has_llm_api_key=bool(_llm_api_key),
            skills_repo_configured=getattr(request.app.state, "remote_skill_repo", None)
            is not None,
            skills_repo_url=app_settings.skills_repo_url,
            skills_repo_branch=app_settings.skills_repo_branch,
            default_model=app_settings.default_model
            if app_settings.default_model
            else settings.llm_model,
            system_prompt=app_settings.system_prompt,
        )


# ── Audit ─────────────────────────────────────────────────────────────────────


class AuditQueryResponse(BaseModel):
    total: int
    items: list[dict]


@app.get("/api/admin/audit", response_model=AuditQueryResponse)
def list_audit_events(
    request: Request,
    current_user: User = Depends(require_admin),
    run_id: str | None = None,
    owner_id: str | None = None,
    skill_name: str | None = None,
    event_type: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> AuditQueryResponse:
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if audit_repo is None:
        return AuditQueryResponse(total=0, items=[])

    from datetime import datetime
    from_ts = datetime.fromisoformat(from_) if from_ else None
    to_ts = datetime.fromisoformat(to) if to else None
    limit = min(limit, 1000)

    # skill_name is only on CommandPolicyEvaluated; filter post-query
    events = audit_repo.query(
        run_id=run_id,
        owner_id=owner_id,
        event_type=event_type,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=100_000,
        offset=0,
    )
    if skill_name is not None:
        events = [e for e in events if getattr(e, "skill_name", None) == skill_name]

    total = len(events)
    page = events[offset: offset + limit]
    
    from app.audit import _event_type_tag
    return AuditQueryResponse(
        total=total,
        items=[{**e.model_dump(mode="json"), "event_type": _event_type_tag(e)} for e in page],
    )


# ── Approval ──────────────────────────────────────────────────────────────────



@app.post("/api/agent/runs/{run_id}/approve", status_code=200)
def approve_command(run_id: str, current_user: User = Depends(get_current_user)) -> dict:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    _assert_run_access(ctx, current_user)
    with _pending_approvals_lock:
        approval = _pending_approvals.pop(run_id, None)
    if approval is None:
        raise HTTPException(
            status_code=404, detail=f"No pending approval for run {run_id!r}"
        )
    approval.actor_id = current_user.user_id
    approval.approved = True
    approval.event.set()
    return {"status": "approved"}


@app.post("/api/agent/runs/{run_id}/deny", status_code=200)
def deny_command(run_id: str, current_user: User = Depends(get_current_user)) -> dict:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    _assert_run_access(ctx, current_user)
    with _pending_approvals_lock:
        approval = _pending_approvals.pop(run_id, None)
    if approval is None:
        raise HTTPException(
            status_code=404, detail=f"No pending approval for run {run_id!r}"
        )
    approval.actor_id = current_user.user_id
    approval.event.set()  # approved stays False
    return {"status": "denied"}


@app.post("/api/agent/runs/{run_id}/abort", status_code=200)
def abort_run(run_id: str, current_user: User = Depends(get_current_user)) -> dict:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    _assert_run_access(ctx, current_user)
    with _workflows_lock:
        workflow = _workflows.get(run_id)
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"No active workflow for run {run_id!r}"
        )
    workflow.abort()
    # Also release any pending approval so the thread unblocks
    with _pending_approvals_lock:
        approval = _pending_approvals.pop(run_id, None)
    if approval:
        approval.event.set()
    return {"status": "aborted"}


# ── Skills ────────────────────────────────────────────────────────────────────


@app.get("/api/skills")
def list_skills(request: Request, current_user: User = Depends(get_current_user)) -> list:
    return [s.model_dump(mode="json") for s in _skills_for_user(request, current_user)]


@app.post("/api/skills/sync")
def sync_remote_skills(request: Request, current_user: User = Depends(require_admin)) -> dict:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo is None:
        raise HTTPException(
            status_code=404, detail="No remote skill repository configured"
        )
    try:
        remote_repo.sync()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"skills": [s.model_dump(mode="json") for s in _all_skills(request)]}


@app.post("/api/skills/generate-policy")
def generate_policy_endpoint(body: GeneratePolicyRequest, request: Request, current_user: User = Depends(require_admin)) -> dict:
    with _settings_lock:
        llm_base_url = _llm_base_url
        llm_api_key = _llm_api_key
    app_settings_repo = getattr(request.app.state, "app_settings_repo", None)
    app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()
    llm_model = app_settings.default_model or settings.llm_model
    planner = Planner(
        request.app.state.skill_repo,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
    )
    try:
        policy = planner.generate_policy(
            skill_name=body.skill_name,
            skill_description=body.skill_description,
            plain_description=body.description,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc
    return {"policy": policy}


@app.get("/api/skills/{skill_id}")
def get_skill(skill_id: str, request: Request, current_user: User = Depends(get_current_user)) -> Any:
    try:
        return request.app.state.skill_repo.get(skill_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} not found")


@app.post("/api/skills", status_code=201)
def create_skill(body: SkillCreateRequest, request: Request, current_user: User = Depends(require_admin)) -> Any:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo:
        remote_names = {s.name.lower() for s in remote_repo.list_skills()}
        if body.name.lower() in remote_names:
            raise HTTPException(
                status_code=409,
                detail=f"A remote skill with name {body.name!r} already exists and cannot be overridden",
            )
    skill = request.app.state.skill_repo.create(
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        policy=body.policy,
        env=body.env,
    )
    return skill.model_dump(mode="json")


@app.patch("/api/skills/{skill_id}")
def patch_skill(skill_id: str, body: SkillPatchRequest, request: Request, current_user: User = Depends(require_admin)) -> Any:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo:
        remote_ids = {s.id for s in remote_repo.list_skills()}
        if skill_id in remote_ids:
            raise HTTPException(
                status_code=404, detail=f"Skill {skill_id!r} is read-only (remote)"
            )
    try:
        kwargs = {}
        if "policy" in body.model_fields_set:
            kwargs["policy"] = body.policy
        if "env" in body.model_fields_set:
            kwargs["env"] = body.env
        skill = request.app.state.skill_repo.update(
            skill_id,
            name=body.name,
            description=body.description,
            enabled=body.enabled,
            **kwargs,
        )
        return skill.model_dump(mode="json")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} not found")


@app.delete("/api/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, request: Request, current_user: User = Depends(require_admin)) -> None:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo:
        remote_ids = {s.id for s in remote_repo.list_skills()}
        if skill_id in remote_ids:
            raise HTTPException(
                status_code=404, detail=f"Skill {skill_id!r} is read-only (remote)"
            )
    try:
        request.app.state.skill_repo.delete(skill_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} not found")


# ── Per-run skill overrides ───────────────────────────────────────────────────


class RunSkillResponse(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    effective_enabled: bool
    policy: str | None
    source: str
    created_at: Any


class RunSkillPatchRequest(BaseModel):
    enabled: bool


def _run_skill_response(skill, overrides: dict[str, bool]) -> RunSkillResponse:
    # Remote skills are always effectively enabled — overrides do not apply.
    if skill.source == "remote":
        effective: bool = True
    else:
        effective = bool(overrides.get(skill.id, skill.enabled))
    return RunSkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        enabled=skill.enabled,
        effective_enabled=effective,
        policy=skill.policy,
        source=skill.source,
        created_at=skill.created_at,
    )


@app.get("/api/agent/runs/{run_id}/skills")
def get_run_skills(run_id: str, request: Request, current_user: User = Depends(get_current_user)) -> list[RunSkillResponse]:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    _assert_run_access(ctx, current_user)
    skills = _skills_for_user(request, current_user)
    return [_run_skill_response(s, ctx.skill_overrides) for s in skills]


@app.patch("/api/agent/runs/{run_id}/skills/{skill_id}")
def patch_run_skill(
    run_id: str,
    skill_id: str,
    body: RunSkillPatchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunSkillResponse:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    _assert_run_access(ctx, current_user)
    skill = next((candidate for candidate in _all_skills(request) if candidate.id == skill_id), None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} not found")
    if skill.source == "remote":
        raise HTTPException(
            status_code=403,
            detail=f"Remote skill {skill_id!r} cannot be deactivated",
        )
    with _runs_lock:
        ctx.skill_overrides[skill_id] = body.enabled
    request.app.state.run_repo.save(ctx)
    return _run_skill_response(skill, ctx.skill_overrides)


# ── API keys ─────────────────────────────────────────────────────────────────


class ApiKeyCreateRequest(BaseModel):
    name: str


@app.post("/api/auth/api-keys", status_code=201, response_model=ApiKeyCreated)
def create_api_key(
    body: ApiKeyCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> ApiKeyCreated:
    """Create a new API key for the authenticated user.

    The full key value is returned **once** in the response and cannot be
    retrieved again — store it securely.
    """
    raw, key_prefix, hashed = generate_api_key()
    api_key_repo: ApiKeyRepository = request.app.state.api_key_repo
    stored = api_key_repo.create(
        user_id=current_user.user_id,
        name=body.name,
        hashed_key=hashed,
        key_prefix=key_prefix,
    )
    return ApiKeyCreated(
        key_id=stored.key_id,
        name=stored.name,
        key_prefix=stored.key_prefix,
        created_at=stored.created_at,
        last_used_at=stored.last_used_at,
        key=raw,
    )


@app.get("/api/auth/api-keys", response_model=list[ApiKeyPublic])
def list_api_keys(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> list[ApiKeyPublic]:
    """List all API keys belonging to the authenticated user."""
    api_key_repo: ApiKeyRepository = request.app.state.api_key_repo
    keys = api_key_repo.list_by_user(current_user.user_id)
    return [
        ApiKeyPublic(
            key_id=k.key_id,
            name=k.name,
            key_prefix=k.key_prefix,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@app.delete("/api/auth/api-keys/{key_id}", status_code=204)
def delete_api_key(
    key_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    """Revoke an API key. Admins may revoke any key; users only their own."""
    api_key_repo: ApiKeyRepository = request.app.state.api_key_repo
    # Admins can revoke any key; regular users are restricted to their own.
    scoped_user_id = None if current_user.role == "admin" else current_user.user_id
    try:
        api_key_repo.delete(key_id, scoped_user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"API key {key_id!r} not found")


# ── Skill check (API-key authenticated) ───────────────────────────────────────


class SkillCheckRequest(BaseModel):
    command: str


class SkillCheckMatch(BaseModel):
    skill_id: str
    name: str
    description: str
    source: str


class SkillCheckResponse(BaseModel):
    command: str
    argv: list[str]
    allowed: bool
    matches: list[SkillCheckMatch]
    reason: str | None = None


def _parse_command(command: str) -> list[str]:
    """Parse a command string into argv, handling basic shell-like quoting."""
    import shlex
    try:
        return shlex.split(command)
    except ValueError:
        # Fallback to simple whitespace split if shlex fails (e.g., unbalanced quotes)
        return command.split()


@app.post("/api/v1/skills/check", response_model=SkillCheckResponse)
def check_skills(
    body: SkillCheckRequest,
    request: Request,
    current_user: User = Depends(get_current_user_or_api_key),
) -> SkillCheckResponse:
    """Check which skills (available to the caller) would allow the given command.

    Accepts authentication via **session cookie** or **X-Api-Key** header.

    The command string is parsed into argv and evaluated against each skill's
    OPA policy. Returns the first skill whose policy allows the command.
    """
    skills = _skills_for_user(request, current_user)
    argv = _parse_command(body.command)
    
    if not argv:
        return SkillCheckResponse(
            command=body.command,
            argv=[],
            allowed=False,
            matches=[],
            reason="Empty command",
        )
    
    # Build a policy evaluator with the user's available skills
    policy = PolicyEvaluator(skills=skills)
    
    # Create a Command object for evaluation
    cmd = Command(argv=argv, rationale="")
    
    # Evaluate against all skills
    allowed, reason, matching_skill = policy.evaluate(cmd)
    
    matches: list[SkillCheckMatch] = []
    if matching_skill is not None:
        matches.append(
            SkillCheckMatch(
                skill_id=matching_skill.id,
                name=matching_skill.name,
                description=matching_skill.description,
                source=matching_skill.source,
            )
        )
    
    return SkillCheckResponse(
        command=body.command,
        argv=argv,
        allowed=allowed,
        matches=matches,
        reason=reason,
    )


# ── Policy Test ───────────────────────────────────────────────────────────────


class PolicyTestRequest(BaseModel):
    rego_policy: str
    test_command: str


class PolicyTestResponse(BaseModel):
    test_id: str
    allowed: bool
    explanation: dict[str, Any] | None
    error: str | None = None


class PolicyTestCaseResponse(BaseModel):
    test_id: str
    rego_policy: str
    test_command: str
    created_at: datetime


@app.post("/api/admin/policy-test", response_model=PolicyTestResponse)
def run_policy_test(
    body: PolicyTestRequest,
    request: Request,
    current_user: User = Depends(require_admin),
) -> PolicyTestResponse:
    """Test a Rego policy against a command string."""
    # Validate non-empty command
    try:
        argv = _parse_command(body.test_command)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    
    if not argv:
        raise HTTPException(status_code=422, detail="test_command must not be empty")
    
    # Evaluate the policy
    result = evaluate_policy_test(rego_policy=body.rego_policy, command_str=body.test_command)
    
    # Store test case
    test_case = request.app.state.policy_test_repo.create(
        user_id=current_user.user_id,
        rego_policy=body.rego_policy,
        test_command=body.test_command,
    )
    
    return PolicyTestResponse(
        test_id=test_case.test_id,
        allowed=result.allowed,
        explanation=result.explanation,
        error=result.error,
    )


@app.get("/api/admin/policy-test", response_model=list[PolicyTestCaseResponse])
def list_policy_tests(
    request: Request,
    current_user: User = Depends(require_admin),
) -> list[PolicyTestCaseResponse]:
    """List all policy test cases for the current user."""
    tests = request.app.state.policy_test_repo.list_for_user(current_user.user_id)
    return [
        PolicyTestCaseResponse(
            test_id=t.test_id,
            rego_policy=t.rego_policy,
            test_command=t.test_command,
            created_at=t.created_at,
        )
        for t in tests
    ]


@app.delete("/api/admin/policy-test/{test_id}", status_code=204)
def delete_policy_test(
    test_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
) -> None:
    """Delete a policy test case."""
    deleted = request.app.state.policy_test_repo.delete(test_id, current_user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Policy test {test_id!r} not found")


# ── Misc ──────────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


# Serve Vite build assets (JS/CSS bundles) — must be registered after API routes
if (_STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")
