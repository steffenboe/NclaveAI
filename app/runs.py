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
        """Persist a single run (upsert). Thread-safe."""
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

    def delete(self, run_id: str) -> None:
        """Delete a single run by id. Thread-safe."""
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            del self._runs[run_id]
            self._save_all()

    def all_as_dict(self) -> dict[str, RunContext]:
        """Return a copy of all runs (by run_id) for populating _runs at startup."""
        with self._lock:
            return {run_id: ctx.model_copy() for run_id, ctx in self._runs.items()}

    def search(self, query: str) -> list[RunContext]:
        """Return all runs whose text fields contain *query* (case-insensitive)."""
        q = query.lower()
        results = []
        with self._lock:
            snapshot = list(self._runs.values())
        for ctx in snapshot:
            if _run_matches(ctx, q):
                results.append(ctx.model_copy())
        return results


def _run_matches(ctx: RunContext, q: str) -> bool:
    if q in (ctx.prompt or "").lower():
        return True
    if q in (ctx.final_message or "").lower():
        return True
    for result in ctx.history:
        if q in " ".join(result.command.argv).lower():
            return True
        if q in (result.command.rationale or "").lower():
            return True
        if q in (result.stdout or "").lower():
            return True
        if q in (result.stderr or "").lower():
            return True
    return False


def _match_hint(ctx: RunContext, q: str) -> str:
    if q in (ctx.prompt or "").lower():
        return "prompt"
    if q in (ctx.final_message or "").lower():
        return "summary"
    for i, result in enumerate(ctx.history):
        if q in " ".join(result.command.argv).lower():
            return f"history[{i}].command"
        if q in (result.command.rationale or "").lower():
            return f"history[{i}].rationale"
        if q in (result.stdout or "").lower():
            return f"history[{i}].stdout"
        if q in (result.stderr or "").lower():
            return f"history[{i}].stderr"
    return "text"

