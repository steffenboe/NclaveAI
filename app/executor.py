from __future__ import annotations

import subprocess

from app.models import ActionResult, Command


class CommandExecutor:
    def run(self, command: Command) -> ActionResult:
        proc = subprocess.run(
            command.argv, capture_output=True, text=True, timeout=30
        )
        return ActionResult(
            command=command,
            allowed=True,
            stdout=proc.stdout.strip() or None,
            stderr=proc.stderr.strip() or None,
            exit_code=proc.returncode,
        )
