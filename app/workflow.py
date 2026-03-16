from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.executor import CommandExecutor
from app.models import RunContext
from app.planner import Planner
from app.policy import PolicyEvaluator

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
    ) -> None:
        self._planner = planner
        self._policy = policy
        self._executor = executor

    def run(
        self,
        prompt: str,
        run_id: str,
        max_iterations: int = 10,
    ) -> RunContext:
        ctx = RunContext(run_id=run_id, prompt=prompt)
        self._log("run_started", ctx, extra={"prompt": prompt})

        for iteration in range(max_iterations):
            # PLAN (LLM step)
            plan_output = self._planner.next_action(ctx)
            self._log("plan", ctx, extra={
                "iteration": iteration,
                "status": plan_output.status,
                "summary": plan_output.summary,
            })

            if plan_output.status in ("done", "failed"):
                ctx.status = plan_output.status
                break

            command = plan_output.command  # guaranteed non-None when status == "action"

            # VALIDATE (OPA step)
            allowed, reason = self._policy.evaluate(command)
            if not allowed:
                self._log("policy_denied", ctx, extra={
                    "argv": command.argv,
                    "reason": reason,
                })
                ctx.status = "policy_denied"
                break

            # EXECUTE (subprocess step)
            result = self._executor.run(command)
            ctx.history.append(result)
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
