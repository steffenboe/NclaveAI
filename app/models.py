from __future__ import annotations

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
    skill_name: str | None = None  # None = denied (no skill claimed the command)
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None


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
