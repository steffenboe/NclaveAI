"""Dedicated secrets store — isolated from process environment.

Secrets are stored in a JSON file (never in os.environ) and only injected
into subprocess environments by the executor at spawn time.
This prevents secrets from leaking via printenv, /proc/self/environ, etc.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SecretsStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._secrets: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            if not isinstance(data, dict):
                raise ValueError("secrets.json root must be a JSON object")
            self._secrets = {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to load secrets from %s: %s", self._path, exc)

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._secrets, indent=2))
        # Best-effort restrictive permissions (owner read/write only)
        try:
            self._path.chmod(0o600)
        except OSError:
            pass

    def list_names(self) -> list[str]:
        """Return secret names (never values)."""
        return sorted(self._secrets.keys())

    def get(self, name: str) -> str | None:
        """Get a secret value by name. Returns None if not found."""
        return self._secrets.get(name)

    def resolve(self, names: list[str]) -> dict[str, str]:
        """Resolve a list of env var names to their secret values.

        Only returns entries that exist in the store.
        """
        return {name: self._secrets[name] for name in names if name in self._secrets}

    def set(self, name: str, value: str) -> None:
        """Set or update a secret."""
        self._secrets[name] = value
        self._save()

    def delete(self, name: str) -> None:
        """Delete a secret. Raises KeyError if not found."""
        if name not in self._secrets:
            raise KeyError(name)
        del self._secrets[name]
        self._save()
