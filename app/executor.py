from __future__ import annotations

import logging
import os
import re
import subprocess

from app.config import settings
from app.models import ActionResult, Command

logger = logging.getLogger(__name__)

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CommandExecutor:
    def run(self, command: Command, env: dict[str, str] | None = None) -> ActionResult:
        resolved_argv = self._resolve_argv(command.argv, env) if env else command.argv
        merged_env = {**os.environ, **env} if env else None
        if env:
            logger.info(
                "Executor: original_argv=%s, resolved_argv=%s, env_keys=%s",
                command.argv,
                resolved_argv,
                list(env.keys()),
            )
        try:
            proc = subprocess.run(
                resolved_argv,
                capture_output=True,
                text=True,
                timeout=settings.command_timeout_seconds,
                env=merged_env,
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

    @staticmethod
    def _resolve_argv(argv: list[str], env: dict[str, str]) -> list[str]:
        """Replace ${VAR_NAME} placeholders in argv with values from env dict."""
        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            return env.get(var_name, match.group(0))  # leave as-is if not in env

        return [_VAR_PATTERN.sub(_replace, arg) for arg in argv]
