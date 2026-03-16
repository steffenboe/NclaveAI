from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.executor import CommandExecutor
from app.models import RunContext, WebhookEvent
from app.planner import Planner
from app.policy import PolicyEvaluator
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    _executor.shutdown(wait=False)


app = FastAPI(title="notesllm-agent", version="0.1.0", lifespan=lifespan)


def _build_workflow() -> AgentWorkflow:
    roles = [r.strip() for r in settings.agent_roles.split(",") if r.strip()]
    return AgentWorkflow(
        planner=Planner(),
        policy=PolicyEvaluator(roles=roles),
        executor=CommandExecutor(),
    )


def _fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ── Manual run ────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    prompt: str


class RunResponse(BaseModel):
    run_id: str
    status: str


@app.post("/api/agent/run", status_code=202, response_model=RunResponse)
def start_run(request: RunRequest) -> RunResponse:
    run_id = str(uuid.uuid4())
    ctx = RunContext(run_id=run_id, prompt=request.prompt, source="manual")

    with _runs_lock:
        _runs[run_id] = ctx

    def _execute() -> None:
        workflow = _build_workflow()
        result = workflow.run(
            prompt=request.prompt,
            run_id=run_id,
            max_iterations=settings.max_iterations,
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

    def _execute(run_id: str, prompt: str, fp: str, event_id: str) -> None:
        try:
            workflow = _build_workflow()
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

    _executor.submit(_execute, run_id, prompt, fp, event_id)

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


# ── Misc ──────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")
