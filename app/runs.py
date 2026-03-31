from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.models import RunContext

logger = logging.getLogger(__name__)


class RunRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._runs: dict[str, RunContext] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning(
                "runs.json not found at %s — starting with empty runs list.",
                self._path,
            )
            return
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"runs.json is not valid JSON: {exc}") from exc

        for raw in data:
            # Restart recovery: runs that were in-flight at shutdown can never resume
            if raw.get("status") in ("running", "waiting_approval"):
                raw["status"] = "failed"
                raw["final_message"] = "Server restarted."
                raw["pending_command"] = None
            ctx = RunContext.model_validate(raw)
            self._runs[ctx.run_id] = ctx

    def _save_all(self) -> None:
        rows = []
        for ctx in self._runs.values():
            d = ctx.model_dump(mode="json")
            d["pending_command"] = None  # transient — never persist
            rows.append(d)
        self._path.write_text(json.dumps(rows, indent=2))

    def save(self, ctx: RunContext) -> None:
        """Persist a single run (upsert). Call this under _runs_lock in main.py."""
        with self._lock:
            self._runs[ctx.run_id] = ctx
            self._save_all()

    def list(self) -> list[RunContext]:
        with self._lock:
            return [ctx.model_copy() for ctx in self._runs.values()]

    def get(self, run_id: str) -> RunContext:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return self._runs[run_id].model_copy()

    def all_as_dict(self) -> dict[str, RunContext]:
        """Return a shallow copy of the internal dict for populating _runs at startup."""
        with self._lock:
            return dict(self._runs)
