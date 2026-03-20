from __future__ import annotations

import json
from pathlib import Path

from regopy import Interpreter

from app.config import settings
from app.models import Command


class PolicyEvaluator:
    def __init__(
        self,
        policy_path: Path | None = None,
        skills: list | None = None,
    ) -> None:
        path = policy_path or settings.policy_path
        self._global_interp = Interpreter()
        self._global_interp.add_module("executor", path.read_text())

        skill_list = skills if skills is not None else []
        self._skill_interps: list[tuple[str, Interpreter]] = []
        for skill in skill_list:
            if skill.policy is not None:
                interp = Interpreter()
                interp.add_module("skill", f"package ops.agent\n{skill.policy}")
                self._skill_interps.append((skill.name, interp))

    def evaluate(self, command: Command) -> tuple[bool, str | None, str | None]:
        """
        Returns (allowed, reason, skill_name).
        skill_name is the name of the skill that permitted the command,
        or None when the global policy is used or the command is denied.

        Evaluation order:
        - If skill policies exist, they are the sole gate (global policy is skipped).
        - If no skill policies are defined, fall back to the global policy.
        """
        input_json = json.dumps({"argv": command.argv})

        # Skill gate: if any skill policy allows the command, permit it immediately
        for skill_name, interp in self._skill_interps:
            interp.set_input_term(input_json)
            out = interp.query("data.ops.agent.allow")
            if out.ok() and '"expressions":[true]' in str(out):
                return True, None, skill_name

        # Fallback: no skill claimed this command — use the global policy
        self._global_interp.set_input_term(input_json)
        output = self._global_interp.query("data.ops.agent.allow")
        if output.ok() and '"expressions":[true]' in str(output):
            return True, None, None
        return False, f"Command {command.argv[0]!r} denied by policy", None
