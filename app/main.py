from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.executor import CommandExecutor
from app.models import Command, RunContext, WebhookEvent
from app.planner import Planner
from app.policy import PolicyEvaluator
from app.skills import SkillRepository
from app.workflow import AgentWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

_STATIC_DIR = Path(__file__).parent / "static"

# In-memory stores
_runs: dict[str, RunContext] = {}
_runs_lock = threading.Lock()

_events: dict[str, WebhookEvent] = {}
_events_lock = threading.Lock()

# Active fingerprints: fingerprint → run_id (for deduplication)
_active_fingerprints: dict[str, str] = {}
_fp_lock = threading.Lock()

# Worker pool (module-level so tests can patch it)
_executor = ThreadPoolExecutor(max_workers=3)

# Approval gate state
_approval_required: bool = False
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
    yield
    _executor.shutdown(wait=False)


app = FastAPI(title="notesllm-agent", version="0.1.0", lifespan=lifespan)


def _build_workflow(
    skill_repo: SkillRepository,
    run_id: str | None = None,
    ctx: RunContext | None = None,
) -> AgentWorkflow:
    enabled_skills = [s for s in skill_repo.list() if s.enabled]
    gate = None
    if run_id is not None and ctx is not None:
        with _settings_lock:
            need_approval = _approval_required
        if need_approval:
            gate = _make_approval_gate(run_id, ctx)
    return AgentWorkflow(
        planner=Planner(skill_repo),
        policy=PolicyEvaluator(skills=enabled_skills),
        executor=CommandExecutor(),
        approval_gate=gate,
    )


def _fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ── Manual run ────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    prompt: str


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


@app.post("/api/agent/run", status_code=202, response_model=RunResponse)
def start_run(request: RunRequest, req: Request) -> RunResponse:
    run_id = str(uuid.uuid4())
    ctx = RunContext(run_id=run_id, prompt=request.prompt, source="manual")
    skill_repo = req.app.state.skill_repo

    with _runs_lock:
        _runs[run_id] = ctx

    def _execute() -> None:
        workflow = _build_workflow(skill_repo, run_id=run_id, ctx=ctx)
        result = workflow.run(
            prompt=request.prompt,
            max_iterations=settings.max_iterations,
            ctx=ctx,
        )
        with _runs_lock:
            _runs[run_id] = result

    thread = threading.Thread(target=_execute, daemon=True)
    thread.start()

    return RunResponse(run_id=run_id, status="running")


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


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsResponse(BaseModel):
    approval_required: bool


class SettingsPatchRequest(BaseModel):
    approval_required: bool


@app.get("/api/settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    with _settings_lock:
        return SettingsResponse(approval_required=_approval_required)


@app.put("/api/settings", response_model=SettingsResponse)
def put_settings(body: SettingsPatchRequest) -> SettingsResponse:
    global _approval_required
    with _settings_lock:
        _approval_required = body.approval_required
        value = _approval_required
    return SettingsResponse(approval_required=value)


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


# ── Webhook intake ────────────────────────────────────────────────────────────

class WebhookResponse(BaseModel):
    event_id: str
    run_id: str
    fingerprint: str
    status: str  # "queued" | "skipped"


@app.post("/api/agent/webhook", response_model=WebhookResponse)
async def receive_webhook(request: Request) -> WebhookResponse:
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"raw": raw.decode(errors="replace")}

    fp = _fingerprint(raw)
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Atomic check-and-set: claim the fingerprint slot or detect duplicate
    run_id = str(uuid.uuid4())
    with _fp_lock:
        existing_run_id = _active_fingerprints.get(fp)
        if existing_run_id is None:
            _active_fingerprints[fp] = run_id  # claim the slot atomically

    if existing_run_id is not None:
        evt = WebhookEvent(
            event_id=event_id,
            received_at=now,
            raw_payload=payload,
            fingerprint=fp,
            run_id=existing_run_id,
            skipped=True,
        )
        with _events_lock:
            _events[event_id] = evt
        return WebhookResponse(
            event_id=event_id,
            run_id=existing_run_id,
            fingerprint=fp,
            status="skipped",
        )

    # Create run context
    prompt = (
        f"[WEBHOOK EVENT]\n"
        f"Received at: {now.isoformat()}\n"
        f"Payload:\n{json.dumps(payload, indent=2)}\n\n"
        f"Determine if action is required and remediate."
    )
    ctx = RunContext(run_id=run_id, prompt=prompt, source="webhook", event_id=event_id)

    evt = WebhookEvent(
        event_id=event_id,
        received_at=now,
        raw_payload=payload,
        fingerprint=fp,
        run_id=run_id,
    )

    with _runs_lock:
        _runs[run_id] = ctx
    with _events_lock:
        _events[event_id] = evt

    skill_repo = request.app.state.skill_repo

    def _execute(run_id: str, prompt: str, fp: str, event_id: str, skill_repo: SkillRepository) -> None:
        try:
            # No run_id/ctx passed → approval gate is always None for webhook runs (by design).
            workflow = _build_workflow(skill_repo)
            result = workflow.run(
                prompt=prompt,
                run_id=run_id,
                max_iterations=settings.max_iterations,
            )
            with _runs_lock:
                _runs[run_id] = result
            with _events_lock:
                if event_id in _events:
                    _events[event_id].run_id = run_id
        finally:
            with _fp_lock:
                _active_fingerprints.pop(fp, None)

    _executor.submit(_execute, run_id, prompt, fp, event_id, skill_repo)

    return WebhookResponse(
        event_id=event_id,
        run_id=run_id,
        fingerprint=fp,
        status="queued",
    )


@app.get("/api/agent/webhooks")
def list_webhooks() -> list[dict]:
    with _events_lock:
        sorted_events = sorted(
            _events.values(),
            key=lambda e: e.received_at,
            reverse=True,
        )
        return [e.model_dump() for e in sorted_events]


@app.get("/api/agent/webhooks/{event_id}")
def get_webhook(event_id: str) -> Any:
    with _events_lock:
        evt = _events.get(event_id)
    if evt is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return evt.model_dump()


# ── Skills ────────────────────────────────────────────────────────────────────

@app.get("/api/skills")
def list_skills(request: Request) -> list:
    return [s.model_dump(mode="json") for s in request.app.state.skill_repo.list()]


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
    try:
        request.app.state.skill_repo.delete(skill_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} not found")


# ── Misc ──────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")
