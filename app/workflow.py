from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from app.executor import CommandExecutor
from app.llm_errors import format_llm_error
from app.models import Command, RunContext  # Command needed for approval_gate type annotation
from app.planner import Planner
from app.policy import PolicyEvaluator
from app.secrets_store import SecretsStore

logger = logging.getLogger(__name__)


class AgentWorkflow:
    """
    THE BLUEPRINT.

    Codified workflow: plan -> validate -> execute -> observe -> repeat.
    The LLM is invoked only inside planner.next_action().
    OPA is invoked only inside policy.evaluate().
    subprocess is invoked only inside executor.run().
    The loop structure, termination, and logging are unconditional code.
    """

    def __init__(
        self,
        planner: Planner,
        policy: PolicyEvaluator,
        executor: CommandExecutor,
        approval_gate: Callable[[Command], bool] | None = None,
        secrets_store: SecretsStore | None = None,
        audit_repo=None,
    ) -> None:
        self._planner = planner
        self._policy = policy
        self._executor = executor
        self._approval_gate = approval_gate
        self._secrets_store = secrets_store
        self._audit_repo = audit_repo
        self._abort = False

    def abort(self) -> None:
        """Signal the workflow to stop after the current step."""
        self._abort = True

    def run(
        self,
        prompt: str,
        run_id: str | None = None,
        max_iterations: int = 10,
        ctx: RunContext | None = None,
    ) -> RunContext:
        if ctx is None:
            if run_id is None:
                raise ValueError("run_id required when ctx is not provided")
            ctx = RunContext(run_id=run_id, prompt=prompt, created_at=datetime.now(timezone.utc))
        self._log("run_started", ctx, extra={"prompt": prompt})

        for iteration in range(max_iterations):
            # Check abort flag
            if self._abort:
                ctx.status = "aborted"
                ctx.final_message = "Run was aborted by user."
                self._log("aborted", ctx)
                break

            # PLAN (LLM step)
            try:
                plan_output = self._planner.next_action(ctx)
            except Exception as exc:
                self._log("planner_error", ctx, extra={"error": str(exc)})
                ctx.status = "failed"
                ctx.final_message = format_llm_error(exc)
                break
            self._log("plan", ctx, extra={
                "iteration": iteration,
                "status": plan_output.status,
                "summary": plan_output.summary,
            })

            if plan_output.status in ("done", "failed"):
                ctx.status = plan_output.status
                ctx.final_message = plan_output.summary
                break

            command = plan_output.command  # guaranteed non-None when status == "action"

            # VALIDATE (OPA step)
            command_id = str(uuid.uuid4())
            allowed, reason, skill = self._policy.evaluate(command, skill_overrides=ctx.skill_overrides)
            approval_required = self._approval_gate is not None
            
            self._emit_policy_event(ctx, command, command_id, skill, allowed, reason, approval_required)
            
            if not allowed:
                self._log("policy_denied", ctx, extra={
                    "argv": command.argv,
                    "reason": reason,
                })
                ctx.status = "policy_denied"
                ctx.final_message = f"Run stopped: '{ ' '.join(command.argv) }' was not approved by policy."
                break

            # HUMAN APPROVAL (optional gate)
            approval_id = None
            if self._approval_gate is not None:
                approval_id = str(uuid.uuid4())
                approved = self._approval_gate(command)
                if not approved:
                    self._emit_approval_event(ctx, command_id, approval_id,
                        actor_id=getattr(ctx, "last_actor_id", None),
                        decision="denied" if not getattr(ctx, "_approval_expired", False) else "expired")
                    self._log("approval_denied", ctx, extra={"argv": command.argv})
                    ctx.status = "policy_denied"
                    ctx.final_message = f"Run stopped: '{ ' '.join(command.argv) }' was not approved."
                    break
                self._emit_approval_event(ctx, command_id, approval_id,
                    actor_id=getattr(ctx, "last_actor_id", None),
                    decision="approved")

            # Resolve per-skill env vars from secrets store (NOT from process env)
            skill_env: dict[str, str] | None = None
            if skill and skill.env and self._secrets_store:
                skill_env = self._secrets_store.resolve(skill.env) or None
                logger.info(
                    "Secrets resolution for skill %r: requested=%s, resolved_keys=%s",
                    skill.name,
                    skill.env,
                    list(skill_env.keys()) if skill_env else [],
                )
            elif skill and skill.env:
                logger.warning(
                    "Skill %r declares env=%s but no secrets store is configured",
                    skill.name,
                    skill.env,
                )

            # EXECUTE (subprocess step)
            result = self._executor.run(command, env=skill_env)
            result.skill_name = skill.name if skill else None
            ctx.history.append(result)
            self._emit_execution_event(ctx, command_id, approval_id, result)
            self._log("action_executed", ctx, extra={
                "argv": command.argv,
                "exit_code": result.exit_code,
                "stdout_preview": (result.stdout or "")[:200],
            })

        else:
            # Loop exhausted without LLM declaring done/failed
            ctx.status = "failed"
            self._log("max_iterations_reached", ctx, extra={
                "max_iterations": max_iterations
            })

        if ctx.status == "policy_denied":
            pass  # final_message already set at point of denial
        elif ctx.final_message is None:
            try:
                ctx.final_message = self._planner.summarize(ctx)
            except Exception as exc:
                self._log("summarize_failed", ctx, extra={"error": str(exc)})

        self._log("run_finished", ctx, extra={"final_status": ctx.status})
        return ctx

    @staticmethod
    def _log(event: str, ctx: RunContext, extra: dict | None = None) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": ctx.run_id,
            "event": event,
            **(extra or {}),
        }
        logger.info(json.dumps(record))

    def _emit_policy_event(self, ctx, command, command_id, skill, allowed, reason, approval_required) -> None:
        if self._audit_repo is None:
            return
        from app.models import CommandPolicyEvaluated
        try:
            self._audit_repo.append(CommandPolicyEvaluated(
                run_id=ctx.run_id,
                owner_id=ctx.owner_id or "",
                command_id=command_id,
                argv=command.argv,
                skill_name=skill.name if skill else None,
                allowed=allowed,
                policy_reason=reason,
                approval_required=approval_required,
            ))
        except Exception as exc:
            logger.warning("Failed to append policy audit event: %s", exc)

    def _emit_approval_event(self, ctx, command_id, approval_id, actor_id, decision) -> None:
        if self._audit_repo is None:
            return
        from app.models import CommandApprovalDecision
        try:
            self._audit_repo.append(CommandApprovalDecision(
                run_id=ctx.run_id,
                owner_id=ctx.owner_id or "",
                command_id=command_id,
                approval_request_id=approval_id,
                actor_id=actor_id,
                decision=decision,
            ))
        except Exception as exc:
            logger.warning("Failed to append approval audit event: %s", exc)

    def _emit_execution_event(self, ctx, command_id, approval_id, result) -> None:
        if self._audit_repo is None:
            return
        from app.models import CommandExecutionFinished
        try:
            self._audit_repo.append(CommandExecutionFinished(
                run_id=ctx.run_id,
                owner_id=ctx.owner_id or "",
                command_id=command_id,
                approval_request_id=approval_id,
                exit_code=result.exit_code if result.exit_code is not None else -1,
                succeeded=(result.exit_code == 0),
            ))
        except Exception as exc:
            logger.warning("Failed to append execution audit event: %s", exc)
