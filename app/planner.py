from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.config import settings
from app.models import ActionResult, PlannerOutput, RunContext
from app.skills import SkillRepository

_RULES = """\
Rules:
- Always read before writing. Observe the current state before making changes.
- Only take the minimum action required. Do not over-correct.
- If the last action's output shows the problem is resolved, return status=done.
- Produce the next action as a single argv list using one of the tools above.
  No shell expansion, no pipes, no redirection.
- Your rationale field is for the audit log only — be concise.

Your response must be a JSON object with these fields:
  - status: "action" | "done" | "failed"
  - summary: one sentence explaining your decision
  - command: {{ argv: [...], rationale: "..." }}  — required when status is "action", omit otherwise
"""

_HUMAN_PROMPT = """\
Problem: {prompt}

Action history so far:
{history_str}

What is the next action? If the goal is achieved, return status=done.
"""

_SUMMARIZE_SYSTEM_PROMPT = """\
You are a developer assistant reporting on a completed autonomous task.

Write a concise user-facing summary (2–4 sentences).
Cover: what was investigated, what actions were taken, and the final outcome.
Use plain language — do not include raw command output or JSON.
"""

_SUMMARIZE_HUMAN_PROMPT = """\
Original request: {prompt}

Final status: {status}

Actions taken:
{history_str}

Write the user-facing summary.
"""

_POLICY_SYSTEM_PROMPT = """\
You are an OPA (Open Policy Agent) Rego expert.

Your task: generate a valid Rego policy body for the given skill.

STRICT RULES:
- Output ONLY bare Rego rule bodies. No `package` line. No markdown fences. No explanation.
- The input object has exactly one field: `input.argv` — a list of strings (the command + arguments).
- Use `allow {{ ... }}` rules. Multiple `allow` rules are OR'd together.
- Keep the policy minimal — only express exactly what the user described.

EXAMPLE (for a skill named "kubectl" that allows only kubectl commands):
allow {{ input.argv[0] == "kubectl" }}

EXAMPLE (for a skill that allows kubectl get and kubectl describe only):
allow {{ input.argv[0] == "kubectl"; input.argv[1] == "get" }}
allow {{ input.argv[0] == "kubectl"; input.argv[1] == "describe" }}
"""

_POLICY_HUMAN_PROMPT = """\
Skill name: {skill_name}
Skill description: {skill_description}

Policy requirement (plain English): {plain_description}

Generate the Rego rule bodies now.
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
    def __init__(self, skill_repo: SkillRepository) -> None:
        self._skill_repo = skill_repo
        llm = ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=0,
        )
        structured_llm = llm.with_structured_output(PlannerOutput)
        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            ("human", _HUMAN_PROMPT),
        ])
        self._chain = prompt | structured_llm

        summarize_prompt = ChatPromptTemplate.from_messages([
            ("system", _SUMMARIZE_SYSTEM_PROMPT),
            ("human", _SUMMARIZE_HUMAN_PROMPT),
        ])
        self._summarize_chain = summarize_prompt | llm | StrOutputParser()

        policy_prompt = ChatPromptTemplate.from_messages([
            ("system", _POLICY_SYSTEM_PROMPT),
            ("human", _POLICY_HUMAN_PROMPT),
        ])
        self._policy_chain = policy_prompt | llm | StrOutputParser()

    def _build_system_prompt(self) -> str:
        enabled = [s for s in self._skill_repo.list() if s.enabled]
        if not enabled:
            return (
                "You are an autonomous developer companion agent.\n\n"
                "No specific tools are pre-configured. Use whatever CLI tools you judge appropriate "
                "(e.g. kubectl, helm, curl). Tool calls are gated by a policy at runtime — "
                "if a command is denied you will see an error in the action history; try an alternative.\n\n"
                + _RULES
            )
        tools_section = "\n\n".join(
            f"[{s.name}]\n{s.description}" for s in enabled
        )
        return (
            "You are an autonomous developer companion agent.\n\n"
            f"The following skills are pre-configured and available:\n\n{tools_section}\n\n"
            "You may also use any standard CLI tool that is appropriate for the task "
            "(e.g. ls, grep, curl, cat, find) — skills are helpers, not an exhaustive list. "
            "Tool calls are gated by a policy at runtime — "
            "if a command is denied you will see an error in the action history; try a different approach or tool.\n\n"
            + _RULES
        )

    def next_action(self, ctx: RunContext) -> PlannerOutput:
        return self._chain.invoke({
            "system_prompt": self._build_system_prompt(),
            "prompt": ctx.prompt,
            "history_str": _format_history(ctx.history),
        })

    def summarize(self, ctx: RunContext) -> str:
        return self._summarize_chain.invoke({
            "prompt": ctx.prompt,
            "status": ctx.status,
            "history_str": _format_history(ctx.history),
        })

    def generate_policy(self, skill_name: str, skill_description: str, plain_description: str) -> str:
        return self._policy_chain.invoke({
            "skill_name": skill_name,
            "skill_description": skill_description,
            "plain_description": plain_description,
        })
