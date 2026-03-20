from __future__ import annotations

import subprocess

from app.config import settings
from app.models import ActionResult, Command


class CommandExecutor:
    def run(self, command: Command) -> ActionResult:
        try:
            proc = subprocess.run(
                command.argv,
                capture_output=True,
                text=True,
                timeout=settings.command_timeout_seconds,
            )
            return ActionResult(
                command=command,
                allowed=True,
                stdout=proc.stdout.strip() or None,
                stderr=proc.stderr.strip() or None,
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return ActionResult(
                command=command,
                allowed=True,
                stderr=f"Command timed out after {settings.command_timeout_seconds}s",
                exit_code=124,
            )
