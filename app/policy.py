from __future__ import annotations

import json

from regopy import Interpreter

from app.config import settings
from app.models import Command


class PolicyEvaluator:
    def __init__(self, roles: list[str]) -> None:
        self._roles = roles
        self._rego = Interpreter()
        self._rego.add_module("executor", settings.policy_path.read_text())

    def evaluate(self, command: Command) -> tuple[bool, str | None]:
        """
        Returns (allowed, reason).
        reason is None when allowed, a denial message when not allowed.
        """
        self._rego.set_input_term(json.dumps({"argv": command.argv, "roles": self._roles}))
        output = self._rego.query("data.ops.agent.allow")
        allowed: bool = output.ok() and '"expressions":[true]' in str(output)
        if allowed:
            return True, None
        reason = f"Command {command.argv[0]!r} not permitted for roles {self._roles}"
        return False, reason
