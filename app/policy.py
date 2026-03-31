from __future__ import annotations

import json
from pathlib import Path

from regopy import Interpreter

from app.config import settings
from app.models import Command
from app.skills import Skill


class PolicyEvaluator:
    def __init__(
        self,
        policy_path: Path | None = None,
        skills: list[Skill] | None = None,
    ) -> None:
        path = policy_path or settings.policy_path
        self._global_interp = Interpreter()
        self._global_interp.add_module("executor", path.read_text())

        # Store (skill_id, skill_name, enabled_globally, interp) for ALL skills with a policy.
        # Enabled-state filtering now happens at evaluate() time so per-run overrides can work.
        skill_list = skills if skills is not None else []
        self._skill_interps: list[tuple[str, str, bool, Interpreter]] = []
        for skill in skill_list:
            if skill.policy is not None:
                interp = Interpreter()
                interp.add_module("skill", f"package ops.agent\n{skill.policy}")
                self._skill_interps.append((skill.id, skill.name, skill.enabled, interp))

    def evaluate(
        self,
        command: Command,
        skill_overrides: dict[str, bool] | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """
        Returns (allowed, reason, skill_name).
        skill_name is the name of the skill that permitted the command,
        or None when the global policy is used or the command is denied.

        Evaluation order:
        - Skill policies are checked first; any skill that is effectively enabled
          (global flag overridden by skill_overrides where present) can allow the command.
        - If no enabled skill's policy allows the command, fall back to the global policy.

        skill_overrides: mapping of skill_id → effective enabled state for this run.
          If a skill ID is absent from the map, the skill's global enabled flag is used.
          Pass None to use global flags for all skills (equivalent to an empty dict).
        """
        overrides = skill_overrides or {}
        input_json = json.dumps({"argv": command.argv})

        for skill_id, skill_name, global_enabled, interp in self._skill_interps:
            effective_enabled = overrides.get(skill_id, global_enabled)
            if not effective_enabled:
                continue
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
