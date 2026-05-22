from __future__ import annotations

import json
import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

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


def _extract_json(text: str) -> str:
    """Extract the first complete JSON object from raw model output.

    Handles leading/trailing prose and markdown code fences.
    """
    # Strip markdown fences first
    text = re.sub(r"```[^\n]*\n", "", text)
    text = text.replace("```", "")

    # Find the outermost { ... } using brace counting
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in model output: {text!r}")
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError(f"Unbalanced braces in model output: {text!r}")


class Planner:
    def __init__(
        self,
        skill_repo: SkillRepository,
        remote_skills: list | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._skill_repo = skill_repo
        self._remote_skills: list = remote_skills or []
        effective_api_key = llm_api_key if llm_api_key is not None else settings.llm_api_key
        llm = ChatOpenAI(
            base_url=llm_base_url or settings.llm_base_url,
            api_key=SecretStr(effective_api_key) if effective_api_key else None,
            model=llm_model or settings.llm_model,
            temperature=0,
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            ("human", _HUMAN_PROMPT),
        ])
        self._chain = prompt | llm | StrOutputParser()

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
        local_enabled = [s for s in self._skill_repo.list() if s.enabled]
        remote_enabled = [s for s in getattr(self, "_remote_skills", []) if s.enabled]
        enabled = remote_enabled + local_enabled
        if not enabled:
            return (
                "You are an autonomous developer companion agent.\n\n"
                "No specific tools are pre-configured. Use whatever CLI tools you judge appropriate "
                "(e.g. kubectl, helm, curl). Tool calls are gated by a policy at runtime — "
                "if a command is denied you will see an error in the action history; try an alternative.\n\n"
                + _RULES
            )

        def _skill_block(s) -> str:
            block = f"[{s.name}]\n{s.description}"
            if s.env:
                env_refs = ", ".join(f"${{{v}}}" for v in s.env)
                block += f"\nEnvironment variables available: {env_refs}. Use ${{VAR}} syntax in arguments — values are injected at runtime."
            return block

        tools_section = "\n\n".join(_skill_block(s) for s in enabled)
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
        raw = self._chain.invoke({
            "system_prompt": self._build_system_prompt(),
            "prompt": ctx.prompt,
            "history_str": _format_history(ctx.history),
        })
        # Tests may inject a PlannerOutput directly via a mock chain
        if isinstance(raw, PlannerOutput):
            return raw
        return PlannerOutput.model_validate(json.loads(_extract_json(raw)))

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
