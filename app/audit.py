from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.models import (
    AuditEvent,
    CommandApprovalDecision,
    CommandExecutionFinished,
    CommandPolicyEvaluated,
)

_EVENT_TYPE_MAP = {
    "command_policy_evaluated": CommandPolicyEvaluated,
    "command_approval_decision": CommandApprovalDecision,
    "command_execution_finished": CommandExecutionFinished,
}

# Each model class knows its own type tag via __name__ → snake_case mapping.
_CLASS_TO_TYPE = {
    CommandPolicyEvaluated: "command_policy_evaluated",
    CommandApprovalDecision: "command_approval_decision",
    CommandExecutionFinished: "command_execution_finished",
}


def _event_type_tag(event: AuditEvent) -> str:
    return _CLASS_TO_TYPE[type(event)]


def _deserialize(line: str) -> AuditEvent:
    raw = json.loads(line)
    tag = raw.get("event_type") or raw.get("_event_type")
    cls = _EVENT_TYPE_MAP.get(tag)
    if cls is None:
        raise ValueError(f"Unknown audit event type: {tag!r}")
    return cls.model_validate(raw)


def _serialize(event: AuditEvent) -> str:
    d = event.model_dump(mode="json")
    d["event_type"] = _event_type_tag(event)
    return json.dumps(d)


@runtime_checkable
class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> None: ...

    def query(
        self,
        run_id: str | None = None,
        owner_id: str | None = None,
        command_id: str | None = None,
        event_type: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]: ...


class FileAuditRepository:
    """Append-only JSONL file audit store. Never rewrites or truncates the file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            with self._path.open("a") as fh:
                fh.write(_serialize(event) + "\n")

    def _load_all(self) -> list[AuditEvent]:
        if not self._path.exists():
            return []
        events: list[AuditEvent] = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(_deserialize(line))
                except Exception:
                    pass  # skip malformed lines; log in production
        return events

    def query(
        self,
        run_id: str | None = None,
        owner_id: str | None = None,
        command_id: str | None = None,
        event_type: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        with self._lock:
            events = self._load_all()

        if run_id is not None:
            events = [e for e in events if e.run_id == run_id]
        if owner_id is not None:
            events = [e for e in events if e.owner_id == owner_id]
        if command_id is not None:
            events = [e for e in events if e.command_id == command_id]
        if event_type is not None:
            events = [e for e in events if _event_type_tag(e) == event_type]
        if from_ts is not None:
            # Normalize timestamps for comparison (handle both aware and naive)
            from_normalized = from_ts.replace(tzinfo=None) if from_ts.tzinfo else from_ts
            events = [e for e in events if (e.timestamp.replace(tzinfo=None) if e.timestamp.tzinfo else e.timestamp) >= from_normalized]
        if to_ts is not None:
            to_normalized = to_ts.replace(tzinfo=None) if to_ts.tzinfo else to_ts
            events = [e for e in events if (e.timestamp.replace(tzinfo=None) if e.timestamp.tzinfo else e.timestamp) <= to_normalized]

        return events[offset: offset + limit]


class MongoAuditRepository:
    """MongoDB audit store. Records are permanent — no delete method exposed."""

    def __init__(self, db) -> None:
        self._col = db["audit_events"]

    def append(self, event: AuditEvent) -> None:
        doc = json.loads(_serialize(event))
        doc["_id"] = event.event_id
        self._col.insert_one(doc)

    def query(
        self,
        run_id: str | None = None,
        owner_id: str | None = None,
        command_id: str | None = None,
        event_type: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        q: dict = {}
        if run_id is not None:
            q["run_id"] = run_id
        if owner_id is not None:
            q["owner_id"] = owner_id
        if command_id is not None:
            q["command_id"] = command_id
        if event_type is not None:
            q["event_type"] = event_type
        if from_ts is not None or to_ts is not None:
            ts_q: dict = {}
            if from_ts is not None:
                ts_q["$gte"] = from_ts.isoformat()
            if to_ts is not None:
                ts_q["$lte"] = to_ts.isoformat()
            q["timestamp"] = ts_q
        result = []
        for doc in self._col.find(q).skip(offset).limit(limit):
            doc.pop("_id", None)
            result.append(_deserialize(json.dumps(doc)))
        return result

