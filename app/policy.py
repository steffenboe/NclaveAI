from __future__ import annotations

import json

from regopy import Interpreter

from app.models import Command
from app.skills import Skill


class PolicyEvaluator:
    def __init__(
        self,
        skills: list[Skill] | None = None,
    ) -> None:
        # Store (skill, interp) for ALL skills with a policy.
        # Enabled-state filtering now happens at evaluate() time so per-run overrides can work.
        skill_list = skills if skills is not None else []
        self._skill_interps: list[tuple[Skill, Interpreter]] = []
        for skill in skill_list:
            if skill.policy is not None:
                interp = Interpreter()
                interp.add_module("skill", f"package ops.agent\n{skill.policy}")
                self._skill_interps.append((skill, interp))

    def evaluate(
        self,
        command: Command,
        skill_overrides: dict[str, bool] | None = None,
    ) -> tuple[bool, str | None, Skill | None]:
        """
        Returns (allowed, reason, skill).
        skill is the Skill object that permitted the command,
        or None when the command is denied.

        Evaluation order:
        - Skill policies are checked first; any skill that is effectively enabled
          (global flag overridden by skill_overrides where present) can allow the command.
        - If no enabled skill's policy allows the command, the command is denied.

        Remote skills are always treated as enabled — overrides do not apply.

        skill_overrides: mapping of skill_id -> effective enabled state for this run.
          If a skill ID is absent from the map, the skill's global enabled flag is used.
          Pass None to use global flags for all skills (equivalent to an empty dict).
        """
        overrides = skill_overrides or {}
        input_json = json.dumps({"argv": command.argv})

        for skill, interp in self._skill_interps:
            # Remote skills are always enabled — overrides do not apply.
            if skill.source == "remote":
                effective_enabled = True
            else:
                effective_enabled = overrides.get(skill.id, skill.enabled)
            if not effective_enabled:
                continue
            interp.set_input_term(input_json)
            out = interp.query("data.ops.agent.allow")
            if out.ok() and '"expressions":[true]' in str(out):
                return True, None, skill

        # No skill claimed this command — deny by default
        return False, f"Command {command.argv[0]!r} denied — no skill policy allows it", None
