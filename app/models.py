from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


class User(BaseModel):
    user_id: str
    username: str
    hashed_password: str
    role: Literal["admin", "user"]
    created_at: datetime
    require_approval: bool = False


class UserPublic(BaseModel):
    user_id: str
    username: str
    role: Literal["admin", "user"]
    created_at: datetime
    require_approval: bool = False


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
    skill_name: str | None = None  # None = denied (no skill claimed the command)
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None


class CommandPolicyEvaluated(BaseModel):
    """Emitted for every command the planner proposes, whether allowed or denied."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    owner_id: str
    command_id: str                  # stable id shared with downstream events
    argv: list[str]                  # raw argv with ${VAR} placeholders — never resolved
    skill_name: str | None = None
    allowed: bool
    policy_reason: str | None = None
    approval_required: bool


class CommandApprovalDecision(BaseModel):
    """Emitted when the human approval gate reaches a decision (or expires)."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    owner_id: str
    command_id: str
    approval_request_id: str         # id of the PendingApproval instance
    actor_id: str | None = None      # null if expired/system-denied
    decision: Literal["approved", "denied", "expired"]
    reason: str | None = None


class CommandExecutionFinished(BaseModel):
    """Emitted only when a command actually executed (policy + approval both passed)."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    owner_id: str
    command_id: str
    approval_request_id: str | None = None   # null when no approval gate was active
    exit_code: int
    succeeded: bool


AuditEvent = CommandPolicyEvaluated | CommandApprovalDecision | CommandExecutionFinished



class RunContext(BaseModel):
    run_id: str
    prompt: str
    history: list[ActionResult] = []
    history_start_index: int = 0
    status: Literal[
        "running", "done", "failed", "policy_denied", "waiting_approval", "aborted"
    ] = "running"
    final_message: str | None = None
    pending_command: dict[str, Any] | None = None
    parent_run_id: str | None = None
    skill_overrides: dict[str, bool] = {}
    llm_model: str | None = None
    owner_id: str | None = None
    last_actor_id: str | None = None   # transient; not persisted (cleared after each command)


class ScheduledTask(BaseModel):
    task_id: str
    owner_id: str
    prompt: str
    cron: str
    timezone: str = "UTC"
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_id: str | None = None
    last_error: str | None = None
