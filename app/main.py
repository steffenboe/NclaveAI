from __future__ import annotations

import copy
import json
import logging
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.auth import create_access_token, get_current_user, hash_password, require_admin, verify_password
from app.config import settings
from app.executor import CommandExecutor
from app.models import Command, RunContext, User, UserPublic
from app.planner import Planner
from app.policy import PolicyEvaluator
from app.runs import RunRepository, _match_hint, _run_matches
from app.secrets_store import SecretsStore
from app.settings_store import AppSettings, AppSettingsRepository
from app.skills import RemoteSkillRepository, SkillRepository
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

        if timed_out:
            with _pending_approvals_lock:
                _pending_approvals.pop(run_id, None)
            ctx.pending_command = None
            return False  # timed_out is authoritative; ignores any late approve

        ctx.pending_command = None
        if approval.approved:
            ctx.status = "running"

        return approval.approved

    return gate


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.skill_repo = SkillRepository(settings.skills_file)
    app_settings_repo = AppSettingsRepository(settings.settings_file)
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
        remote_repo = RemoteSkillRepository(
            app_settings.skills_repo_url,
            branch=app_settings.skills_repo_branch,
        )
        try:
            remote_repo.sync()
            app.state.remote_skill_repo = remote_repo
            logger.info("Remote skills loaded from %s", app_settings.skills_repo_url)
        except Exception as exc:
            logger.warning("Failed to load remote skills: %s", exc)

    run_repo = RunRepository(settings.runs_file)
    app.state.run_repo = run_repo
    app.state.secrets_store = SecretsStore(settings.secrets_file)
    with _runs_lock:
        _runs.update(run_repo.all_as_dict())

    user_repo = UserRepository(settings.users_file)
    app.state.user_repo = user_repo
    if user_repo.count() == 0 and settings.admin_password:
        user_repo.create(
            username=settings.admin_username,
            hashed_password=hash_password(settings.admin_password),
            role="admin",
        )
        logger.info("Bootstrapped admin user %r", settings.admin_username)

    yield


app = FastAPI(title="notesllm-agent", version="0.1.0", lifespan=lifespan)


def _all_skills(request: Request) -> list:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    remote = remote_repo.list_skills() if remote_repo else []
    local = request.app.state.skill_repo.list()
    return remote + local


def _build_workflow(
    skill_repo: SkillRepository,
    run_id: str | None = None,
    ctx: RunContext | None = None,
    remote_skill_repo=None,
    secrets_store: SecretsStore | None = None,
    user_require_approval: bool = False,
) -> AgentWorkflow:
    local_skills = skill_repo.list()
    remote_skills = remote_skill_repo.list_skills() if remote_skill_repo else []
    all_skills = remote_skills + local_skills
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
    return AgentWorkflow(
        planner=Planner(
            skill_repo,
            remote_skills=remote_skills,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
        ),
        policy=PolicyEvaluator(skills=all_skills),
        executor=CommandExecutor(),
        approval_gate=gate,
        secrets_store=secrets_store,
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
    run_id = str(uuid.uuid4())

    seeded_history: list = []
    history_start_index = 0
    seeded_overrides: dict[str, bool] = {}
    parent_run_id: str | None = None
    seeded_model: str | None = request.llm_model
    history_snapshot: list = []
    if request.context_run_id is not None:
        with _runs_lock:
            parent_ctx = _runs.get(request.context_run_id)
            if parent_ctx is not None:
                if current_user.role != "admin" and parent_ctx.owner_id != current_user.user_id:
                    raise HTTPException(status_code=404, detail=f"Run {request.context_run_id!r} not found")
                history_snapshot = list(parent_ctx.history)
                seeded_overrides = dict(parent_ctx.skill_overrides)
                parent_run_id = request.context_run_id
                # Inherit model from parent if not explicitly set
                if seeded_model is None:
                    seeded_model = parent_ctx.llm_model
        if parent_run_id is not None:
            seeded_history = copy.deepcopy(history_snapshot)
            history_start_index = len(seeded_history)

    ctx = RunContext(
        run_id=run_id,
        prompt=request.prompt,
        history=seeded_history,
        history_start_index=history_start_index,
        skill_overrides=seeded_overrides,
        parent_run_id=parent_run_id,
        llm_model=seeded_model,
        owner_id=current_user.user_id,
    )
    skill_repo = req.app.state.skill_repo
    remote_skill_repo = getattr(req.app.state, "remote_skill_repo", None)
    run_repo = req.app.state.run_repo

    # Resolve model: explicit request > parent context > app settings default > config default
    if seeded_model is None:
        app_settings_repo = getattr(req.app.state, "app_settings_repo", None)
        app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()
        seeded_model = app_settings.default_model or settings.llm_model
    ctx.llm_model = seeded_model

    with _runs_lock:
        _runs[run_id] = ctx
    run_repo.save(ctx)

    def _execute() -> None:
        workflow = _build_workflow(
            skill_repo, run_id=run_id, ctx=ctx, remote_skill_repo=remote_skill_repo,
            secrets_store=req.app.state.secrets_store,
            user_require_approval=current_user.require_approval,
        )
        with _workflows_lock:
            _workflows[run_id] = workflow
        try:
            result = workflow.run(
                prompt=request.prompt,
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
        if current_user.role != "admin" and ctx.owner_id != current_user.user_id:
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
    if current_user.role != "admin":
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


# ── Models ────────────────────────────────────────────────────────────────────


class ModelsResponse(BaseModel):
    available_models: list[str]
    default_model: str


@app.get("/api/models", response_model=ModelsResponse)
def get_models(request: Request, current_user: User = Depends(get_current_user)) -> ModelsResponse:
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
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"API returned {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach LLM API: {exc}") from exc

    available_models = sorted(m["id"] for m in data.get("data", []) if "id" in m)
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


class SettingsPatchRequest(BaseModel):
    approval_required: bool | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    skills_repo_url: str | None = None
    skills_repo_branch: str | None = None
    default_model: str | None = None


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
        raise HTTPException(
            status_code=503,
            detail="Application settings repository is unavailable",
        )
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
    return [s.model_dump(mode="json") for s in _all_skills(request)]


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
        effective = True
    else:
        effective = overrides.get(skill.id, skill.enabled)
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
    skills = _all_skills(request)
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
