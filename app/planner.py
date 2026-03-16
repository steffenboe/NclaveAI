from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.config import settings
from app.models import ActionResult, PlannerOutput, RunContext

_SYSTEM_PROMPT = """\
You are an autonomous operations agent for the notesllm application.

You are given a problem description and a history of actions already taken.
Your job is to decide the SINGLE NEXT action needed to make progress toward
solving the problem, or to declare that the goal is achieved (done) or
unresolvable (failed).

You have access to standard Kubernetes CLI tools available in the execution
environment (e.g. kubectl, helm). When status is "action", produce the exact
command to run as an argv list — no shell expansion, no pipes, no redirection.

Rules:
- Always read before writing. Observe the current state before making changes.
- Only take the minimum action required. Do not over-correct.
- If the last action's output shows the problem is resolved, return status=done.
- If you have tried 3+ actions without progress, return status=failed.
- Your rationale field is for the audit log only — be concise.

Your response must be a JSON object with these fields:
  - status: "action" | "done" | "failed"
  - summary: one sentence explaining your decision
  - command: {{ argv: [...], rationale: "..." }}  — required when status is "action", omit otherwise

When the prompt starts with "[WEBHOOK EVENT]", you are responding to an
automated external event. Analyze the JSON payload to determine if any
Kubernetes resources require corrective action. If no action is needed
(e.g. the event is informational), return status=done immediately with
a brief explanation in the summary field.
"""

_HUMAN_PROMPT = """\
Problem: {prompt}

Action history so far:
{history_str}

What is the next action? If the goal is achieved, return status=done.
"""

_SUMMARIZE_SYSTEM_PROMPT = """\
You are an operations assistant reporting on a completed autonomous Kubernetes task.

Write a concise user-facing summary (2–4 sentences).
Cover: what was investigated, what actions were taken, and the final outcome.
Use plain language — do not include raw kubectl output or JSON.
"""

_SUMMARIZE_HUMAN_PROMPT = """\
Original request: {prompt}

Final status: {status}

Actions taken:
{history_str}

Write the user-facing summary.
"""


def _format_history(history: list[ActionResult]) -> str:
    if not history:
        return "None — this is the first action."
    lines = []
    for i, r in enumerate(history, 1):
        lines.append(
            f"{i}. argv={r.command.argv} exit_code={r.exit_code}\n"
            f"   stdout: {r.stdout or '(empty)'}\n"
            f"   stderr: {r.stderr or '(empty)'}"
        )
    return "\n".join(lines)


class Planner:
    def __init__(self) -> None:
        llm = ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=0,
        )
        structured_llm = llm.with_structured_output(PlannerOutput)
        prompt = ChatPromptTemplate.from_messages([
            ("system", _SYSTEM_PROMPT),
            ("human", _HUMAN_PROMPT),
        ])
        self._chain = prompt | structured_llm

        summarize_prompt = ChatPromptTemplate.from_messages([
            ("system", _SUMMARIZE_SYSTEM_PROMPT),
            ("human", _SUMMARIZE_HUMAN_PROMPT),
        ])
        self._summarize_chain = summarize_prompt | llm | StrOutputParser()

    def next_action(self, ctx: RunContext) -> PlannerOutput:
        return self._chain.invoke({
            "prompt": ctx.prompt,
            "history_str": _format_history(ctx.history),
        })

    def summarize(self, ctx: RunContext) -> str:
        return self._summarize_chain.invoke({
            "prompt": ctx.prompt,
            "status": ctx.status,
            "history_str": _format_history(ctx.history),
        })
