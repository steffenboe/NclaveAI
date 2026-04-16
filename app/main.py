from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings
from app.executor import CommandExecutor
from app.models import Command, RunContext
from app.planner import Planner
from app.policy import PolicyEvaluator
from app.runs import RunRepository, _match_hint, _run_matches
from app.settings_store import AppSettings, AppSettingsRepository
from app.skills import RemoteSkillRepository, SkillRepository
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
    with _runs_lock:
        _runs.update(run_repo.all_as_dict())
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
) -> AgentWorkflow:
    local_skills = skill_repo.list()
    remote_skills = remote_skill_repo.list_skills() if remote_skill_repo else []
    all_skills = remote_skills + local_skills
    gate = None
    llm_base_url = settings.llm_base_url
    llm_api_key = settings.llm_api_key
    if run_id is not None and ctx is not None:
        with _settings_lock:
            need_approval = _approval_required
            llm_base_url = _llm_base_url
            llm_api_key = _llm_api_key
        if need_approval:
            gate = _make_approval_gate(run_id, ctx)
    return AgentWorkflow(
        planner=Planner(
            skill_repo,
            remote_skills=remote_skills,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=settings.llm_model,
        ),
        policy=PolicyEvaluator(skills=all_skills),
        executor=CommandExecutor(),
        approval_gate=gate,
    )


# ── Manual run ────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    prompt: str
    context_run_id: str | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    enabled: bool = True
    policy: str | None = None


class SkillPatchRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    policy: str | None = None


class GeneratePolicyRequest(BaseModel):
    skill_name: str
    skill_description: str
    description: str


@app.post("/api/agent/run", status_code=202, response_model=RunResponse)
def start_run(request: RunRequest, req: Request) -> RunResponse:
    run_id = str(uuid.uuid4())

    seeded_history: list = []
    history_start_index = 0
    seeded_overrides: dict[str, bool] = {}
    parent_run_id: str | None = None
    history_snapshot: list = []
    if request.context_run_id is not None:
        with _runs_lock:
            parent_ctx = _runs.get(request.context_run_id)
            if parent_ctx is not None:
                history_snapshot = list(parent_ctx.history)
                seeded_overrides = dict(parent_ctx.skill_overrides)
                parent_run_id = request.context_run_id
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
    )
    skill_repo = req.app.state.skill_repo
    remote_skill_repo = getattr(req.app.state, "remote_skill_repo", None)
    run_repo = req.app.state.run_repo

    with _runs_lock:
        _runs[run_id] = ctx
    run_repo.save(ctx)

    def _execute() -> None:
        workflow = _build_workflow(skill_repo, run_id=run_id, ctx=ctx, remote_skill_repo=remote_skill_repo)
        result = workflow.run(
            prompt=request.prompt,
            max_iterations=settings.max_iterations,
            ctx=ctx,
        )
        with _runs_lock:
            _runs[run_id] = result
        run_repo.save(result)

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
def search_runs(q: str = "") -> list[SearchHit]:
    """Full-text search across all runs (prompt, summary, command history)."""
    if not q.strip():
        return []
    query = q.lower()
    with _runs_lock:
        runs_snapshot = list(_runs.values())
    hits: list[SearchHit] = []
    for ctx in runs_snapshot:
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
def get_run(run_id: str) -> Any:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return ctx.model_dump()


@app.get("/api/agent/runs")
def list_runs() -> list[dict]:
    with _runs_lock:
        return [ctx.model_dump() for ctx in _runs.values()]


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
                run_id
                for run_id, ctx in _runs.items()
                if ctx.parent_run_id == current
            ]
        to_visit.extend(children)
    return collected


@app.delete("/api/agent/runs/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request) -> None:
    with _runs_lock:
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

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


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsResponse(BaseModel):
    approval_required: bool
    llm_base_url: str
    has_llm_api_key: bool
    skills_repo_configured: bool
    skills_repo_url: str | None
    skills_repo_branch: str


class SettingsPatchRequest(BaseModel):
    approval_required: bool | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    skills_repo_url: str | None = None
    skills_repo_branch: str | None = None


@app.get("/api/settings", response_model=SettingsResponse)
def get_settings(request: Request) -> SettingsResponse:
    app_settings_repo = getattr(request.app.state, "app_settings_repo", None)
    app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()
    with _settings_lock:
        return SettingsResponse(
            approval_required=_approval_required,
            llm_base_url=_llm_base_url,
            has_llm_api_key=bool(_llm_api_key),
            skills_repo_configured=getattr(request.app.state, "remote_skill_repo", None) is not None,
            skills_repo_url=app_settings.skills_repo_url,
            skills_repo_branch=app_settings.skills_repo_branch,
        )


@app.put("/api/settings", response_model=SettingsResponse)
def put_settings(body: SettingsPatchRequest, request: Request) -> SettingsResponse:
    global _approval_required, _llm_base_url, _llm_api_key

    # ── LLM settings (in-memory, unchanged) ──────────────────────────────────
    with _settings_lock:
        if body.approval_required is not None:
            _approval_required = body.approval_required

        if body.llm_base_url is not None:
            trimmed_url = body.llm_base_url.strip()
            if not trimmed_url:
                raise HTTPException(status_code=422, detail="llm_base_url must not be empty")
            _llm_base_url = trimmed_url

        if body.llm_api_key is not None:
            _llm_api_key = body.llm_api_key.strip()

    # ── Repo settings (persisted) ─────────────────────────────────────────────
    if "skills_repo_url" in body.model_fields_set:
        app_settings_repo: AppSettingsRepository = request.app.state.app_settings_repo
        app_settings = app_settings_repo.load()
        new_url = body.skills_repo_url.strip() if body.skills_repo_url else None
        new_branch = body.skills_repo_branch.strip() if body.skills_repo_branch else app_settings.skills_repo_branch
        app_settings = AppSettings(skills_repo_url=new_url, skills_repo_branch=new_branch)
        app_settings_repo.save(app_settings)

        if new_url:
            remote_repo = RemoteSkillRepository(new_url, branch=new_branch)
            try:
                remote_repo.sync()
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            request.app.state.remote_skill_repo = remote_repo
        else:
            request.app.state.remote_skill_repo = None

    app_settings_repo = getattr(request.app.state, "app_settings_repo", None)
    app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()

    with _settings_lock:
        return SettingsResponse(
            approval_required=_approval_required,
            llm_base_url=_llm_base_url,
            has_llm_api_key=bool(_llm_api_key),
            skills_repo_configured=getattr(request.app.state, "remote_skill_repo", None) is not None,
            skills_repo_url=app_settings.skills_repo_url,
            skills_repo_branch=app_settings.skills_repo_branch,
        )


# ── Approval ──────────────────────────────────────────────────────────────────

@app.post("/api/agent/runs/{run_id}/approve", status_code=200)
def approve_command(run_id: str) -> dict:
    with _pending_approvals_lock:
        approval = _pending_approvals.pop(run_id, None)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"No pending approval for run {run_id!r}")
    approval.approved = True
    approval.event.set()
    return {"status": "approved"}


@app.post("/api/agent/runs/{run_id}/deny", status_code=200)
def deny_command(run_id: str) -> dict:
    with _pending_approvals_lock:
        approval = _pending_approvals.pop(run_id, None)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"No pending approval for run {run_id!r}")
    approval.event.set()  # approved stays False
    return {"status": "denied"}


# ── Skills ────────────────────────────────────────────────────────────────────

@app.get("/api/skills")
def list_skills(request: Request) -> list:
    return [s.model_dump(mode="json") for s in _all_skills(request)]


@app.post("/api/skills/sync")
def sync_remote_skills(request: Request) -> dict:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo is None:
        raise HTTPException(status_code=404, detail="No remote skill repository configured")
    try:
        remote_repo.sync()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"skills": [s.model_dump(mode="json") for s in _all_skills(request)]}


@app.post("/api/skills/generate-policy")
def generate_policy_endpoint(body: GeneratePolicyRequest, request: Request) -> dict:
    with _settings_lock:
        llm_base_url = _llm_base_url
        llm_api_key = _llm_api_key
    planner = Planner(
        request.app.state.skill_repo,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=settings.llm_model,
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
def get_skill(skill_id: str, request: Request) -> Any:
    try:
        return request.app.state.skill_repo.get(skill_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} not found")


@app.post("/api/skills", status_code=201)
def create_skill(body: SkillCreateRequest, request: Request) -> Any:
    skill = request.app.state.skill_repo.create(
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        policy=body.policy,
    )
    return skill.model_dump(mode="json")


@app.patch("/api/skills/{skill_id}")
def patch_skill(skill_id: str, body: SkillPatchRequest, request: Request) -> Any:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo:
        remote_ids = {s.id for s in remote_repo.list_skills()}
        if skill_id in remote_ids:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} is read-only (remote)")
    try:
        kwargs = {}
        if "policy" in body.model_fields_set:
            kwargs["policy"] = body.policy
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
def delete_skill(skill_id: str, request: Request) -> None:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo:
        remote_ids = {s.id for s in remote_repo.list_skills()}
        if skill_id in remote_ids:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} is read-only (remote)")
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
    return RunSkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        enabled=skill.enabled,
        effective_enabled=overrides.get(skill.id, skill.enabled),
        policy=skill.policy,
        source=skill.source,
        created_at=skill.created_at,
    )


@app.get("/api/agent/runs/{run_id}/skills")
def get_run_skills(run_id: str, request: Request) -> list[RunSkillResponse]:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    skills = _all_skills(request)
    return [_run_skill_response(s, ctx.skill_overrides) for s in skills]


@app.patch("/api/agent/runs/{run_id}/skills/{skill_id}")
def patch_run_skill(
    run_id: str,
    skill_id: str,
    body: RunSkillPatchRequest,
    request: Request,
) -> RunSkillResponse:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    skill = next((candidate for candidate in _all_skills(request) if candidate.id == skill_id), None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} not found")
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
