from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


class Command(BaseModel):
    argv: Annotated[list[str], Field(min_length=1)]
    rationale: str


class PlannerOutput(BaseModel):
    status: Literal["action", "done", "failed"]
    command: Command | None = None
    summary: str

    @model_validator(mode="after")
    def command_required_when_action(self) -> "PlannerOutput":
        if self.status == "action" and self.command is None:
            raise ValueError("command is required when status is 'action'")
        return self


class ActionResult(BaseModel):
    command: Command
    allowed: bool
    policy_reason: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None


class WebhookEvent(BaseModel):
    event_id: str
    received_at: datetime
    raw_payload: dict[str, Any]
    fingerprint: str        # SHA-256 hex of raw body
    run_id: str | None = None
    skipped: bool = False


class RunContext(BaseModel):
    run_id: str
    prompt: str
    history: list[ActionResult] = []
    status: Literal["running", "done", "failed", "policy_denied"] = "running"
    final_message: str | None = None
    source: Literal["manual", "webhook"] = "manual"
    event_id: str | None = None
