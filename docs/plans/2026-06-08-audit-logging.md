# Audit Logging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a compliance-grade, tamper-evident audit trail that records every command execution attempt as a sequence of immutable, linked audit events stored separately from runs — so deletion of a run never erases its audit history.

**Architecture:** Three distinct event types share a `run_id` and `command_id` (and optionally `approval_request_id`) to form a traceable chain per command attempt:

- `command_policy_evaluated` — always emitted; records argv, skill, OPA result, whether approval is required
- `command_approval_decision` — emitted only when the approval gate fires; records actor, decision, timestamp, optional reason
- `command_execution_finished` — emitted only when the command actually ran; records exit code and result

This split cleanly models every edge case:
- Policy denied → policy event only
- Human denied → policy + approval events, no execution event
- Human approved, execution failed → all three events, failure in execution event
- Approval expired → policy event + approval event with `decision=expired`, no execution event

Two concrete `AuditRepository` implementations: `FileAuditRepository` (append-only `.jsonl`, never rewritten) and `MongoAuditRepository` (`audit_events` collection, no TTL). A new admin-only `GET /api/admin/audit` endpoint exposes the queryable log.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, pytest + pytest-asyncio, pymongo (optional)

---

### Task 1: Audit event models

**Files:**
- Modify: `app/models.py`

**Step 1: Write the failing tests**

```python
# tests/test_models.py — add to existing file
import uuid as _uuid

def test_command_policy_evaluated_defaults():
    from app.models import CommandPolicyEvaluated
    e = CommandPolicyEvaluated(
        run_id="r1",
        owner_id="u1",
        command_id="c1",
        argv=["ls", "-la"],
        allowed=True,
        approval_required=False,
    )
    assert e.event_id
    assert e.timestamp
    assert e.skill_name is None
    assert e.policy_reason is None


def test_command_approval_decision_defaults():
    from app.models import CommandApprovalDecision
    e = CommandApprovalDecision(
        run_id="r1",
        owner_id="u1",
        command_id="c1",
        approval_request_id="a1",
        actor_id="user-42",
        decision="approved",
    )
    assert e.event_id
    assert e.reason is None


def test_command_execution_finished_defaults():
    from app.models import CommandExecutionFinished
    e = CommandExecutionFinished(
        run_id="r1",
        owner_id="u1",
        command_id="c1",
        exit_code=0,
        succeeded=True,
    )
    assert e.event_id
    assert e.approval_request_id is None
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_models.py::test_command_policy_evaluated_defaults \
       tests/test_models.py::test_command_approval_decision_defaults \
       tests/test_models.py::test_command_execution_finished_defaults -v
```

Expected: `ImportError` — models do not exist yet.

**Step 3: Add models to `app/models.py`**

Add after the existing `ActionResult` class. Also add to the top-level imports:
```python
import uuid
from datetime import timezone
```

Then add:

```python
class CommandPolicyEvaluated(BaseModel):
    """Emitted for every command the planner proposes, whether allowed or denied."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    owner_id: str
    command_id: str                  # stable id shared with downstream events
    argv: list[str]                  # raw argv with ${VAR} placeholders — never resolved
    skill_name: str | None = None
    allowed: bool
    policy_reason: str | None = None
    approval_required: bool


class CommandApprovalDecision(BaseModel):
    """Emitted when the human approval gate reaches a decision (or expires)."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    owner_id: str
    command_id: str
    approval_request_id: str         # id of the PendingApproval instance
    actor_id: str | None = None      # null if expired/system-denied
    decision: Literal["approved", "denied", "expired"]
    reason: str | None = None


class CommandExecutionFinished(BaseModel):
    """Emitted only when a command actually executed (policy + approval both passed)."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    owner_id: str
    command_id: str
    approval_request_id: str | None = None   # null when no approval gate was active
    exit_code: int
    succeeded: bool


AuditEvent = CommandPolicyEvaluated | CommandApprovalDecision | CommandExecutionFinished
```

**Step 4: Run tests**

```bash
pytest tests/test_models.py -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat(audit): add CommandPolicyEvaluated, CommandApprovalDecision, CommandExecutionFinished models"
```

---

### Task 2: `AuditRepository` — file backend

**Files:**
- Create: `app/audit.py`
- Create: `tests/test_audit.py`

**Step 1: Write failing tests**

```python
# tests/test_audit.py
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from app.models import CommandPolicyEvaluated, CommandApprovalDecision, CommandExecutionFinished
from app.audit import FileAuditRepository


def _policy_event(**kwargs) -> CommandPolicyEvaluated:
    defaults = dict(
        run_id="run-1", owner_id="user-1", command_id="cmd-1",
        argv=["ls"], allowed=True, approval_required=False,
    )
    defaults.update(kwargs)
    return CommandPolicyEvaluated(**defaults)


def _approval_event(**kwargs) -> CommandApprovalDecision:
    defaults = dict(
        run_id="run-1", owner_id="user-1", command_id="cmd-1",
        approval_request_id="req-1", actor_id="user-1", decision="approved",
    )
    defaults.update(kwargs)
    return CommandApprovalDecision(**defaults)


def _execution_event(**kwargs) -> CommandExecutionFinished:
    defaults = dict(
        run_id="run-1", owner_id="user-1", command_id="cmd-1",
        exit_code=0, succeeded=True,
    )
    defaults.update(kwargs)
    return CommandExecutionFinished(**defaults)


def test_append_and_query_all(tmp_path):
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    e = _policy_event()
    repo.append(e)
    results = repo.query()
    assert len(results) == 1
    assert results[0].event_id == e.event_id


def test_file_is_strictly_append_only(tmp_path):
    """Startup and query must never rewrite or truncate audit.jsonl."""
    path = tmp_path / "audit.jsonl"
    repo = FileAuditRepository(path)
    repo.append(_policy_event(command_id="cmd-1"))
    repo.append(_policy_event(command_id="cmd-2"))
    original_lines = path.read_text().splitlines()
    assert len(original_lines) == 2

    # Instantiate a second repo over the same file and query — must not truncate
    repo2 = FileAuditRepository(path)
    repo2.query()
    assert path.read_text().splitlines() == original_lines


def test_query_filter_owner_id(tmp_path):
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    repo.append(_policy_event(owner_id="alice"))
    repo.append(_policy_event(owner_id="bob"))
    assert len(repo.query(owner_id="alice")) == 1
    assert len(repo.query(owner_id="bob")) == 1


def test_query_filter_run_id(tmp_path):
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    repo.append(_policy_event(run_id="run-A"))
    repo.append(_policy_event(run_id="run-B"))
    assert len(repo.query(run_id="run-A")) == 1


def test_query_filter_event_type(tmp_path):
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    repo.append(_policy_event())
    repo.append(_approval_event())
    repo.append(_execution_event())
    assert len(repo.query(event_type="command_policy_evaluated")) == 1
    assert len(repo.query(event_type="command_approval_decision")) == 1
    assert len(repo.query(event_type="command_execution_finished")) == 1


def test_query_filter_command_id(tmp_path):
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    repo.append(_policy_event(command_id="cmd-X"))
    repo.append(_policy_event(command_id="cmd-Y"))
    assert len(repo.query(command_id="cmd-X")) == 1


def test_query_time_range(tmp_path):
    path = tmp_path / "audit.jsonl"
    now = datetime.now(timezone.utc)
    old = _policy_event(command_id="old")
    old = old.model_copy(update={"timestamp": now - timedelta(hours=2)})
    recent = _policy_event(command_id="recent")
    path.write_text(old.model_dump_json() + "\n" + recent.model_dump_json() + "\n")
    repo = FileAuditRepository(path)
    results = repo.query(from_ts=now - timedelta(hours=1))
    assert len(results) == 1
    assert results[0].command_id == "recent"


def test_query_limit_offset(tmp_path):
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    for i in range(5):
        repo.append(_policy_event(command_id=f"cmd-{i}"))
    assert len(repo.query(limit=3)) == 3
    assert len(repo.query(limit=3, offset=3)) == 2


def test_deletion_invariant(tmp_path):
    """Deleting audit events for a run_id is impossible — query still returns them."""
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    repo.append(_policy_event(run_id="doomed-run"))
    repo.append(_execution_event(run_id="doomed-run"))
    # Simulate run deletion (no delete method exists on AuditRepository)
    assert not hasattr(repo, "delete")
    results = repo.query(run_id="doomed-run")
    assert len(results) == 2


def test_mixed_event_types_round_trip(tmp_path):
    """All three event types can be appended and queried from the same file."""
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    repo.append(_policy_event())
    repo.append(_approval_event())
    repo.append(_execution_event())
    all_events = repo.query()
    assert len(all_events) == 3
    types = {type(e).__name__ for e in all_events}
    assert types == {"CommandPolicyEvaluated", "CommandApprovalDecision", "CommandExecutionFinished"}
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_audit.py -v
```

Expected: `ModuleNotFoundError` — `app.audit` does not exist.

**Step 3: Create `app/audit.py`**

```python
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
            events = [e for e in events if e.timestamp >= from_ts]
        if to_ts is not None:
            events = [e for e in events if e.timestamp <= to_ts]

        return events[offset: offset + limit]
```

**Step 4: Run tests**

```bash
pytest tests/test_audit.py -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add app/audit.py tests/test_audit.py
git commit -m "feat(audit): add FileAuditRepository with 3-event schema"
```

---

### Task 3: `MongoAuditRepository`

**Files:**
- Modify: `app/audit.py`
- Modify: `tests/test_mongo_repos.py`

**Step 1: Write failing tests**

Check `tests/test_mongo_repos.py` for the existing `mongo_db` fixture, then add:

```python
# tests/test_mongo_repos.py — add alongside existing Mongo tests
def test_mongo_audit_append_and_query(mongo_db):
    from app.audit import MongoAuditRepository
    from app.models import CommandPolicyEvaluated

    repo = MongoAuditRepository(mongo_db)
    e = CommandPolicyEvaluated(
        run_id="r1", owner_id="u1", command_id="c1",
        argv=["ls"], allowed=True, approval_required=False,
    )
    repo.append(e)
    results = repo.query()
    assert len(results) == 1
    assert results[0].event_id == e.event_id
    assert type(results[0]).__name__ == "CommandPolicyEvaluated"


def test_mongo_audit_mixed_types_round_trip(mongo_db):
    from app.audit import MongoAuditRepository
    from app.models import CommandPolicyEvaluated, CommandApprovalDecision, CommandExecutionFinished

    repo = MongoAuditRepository(mongo_db)
    repo.append(CommandPolicyEvaluated(
        run_id="r1", owner_id="u1", command_id="c1",
        argv=["ls"], allowed=True, approval_required=True,
    ))
    repo.append(CommandApprovalDecision(
        run_id="r1", owner_id="u1", command_id="c1",
        approval_request_id="req-1", actor_id="u1", decision="approved",
    ))
    repo.append(CommandExecutionFinished(
        run_id="r1", owner_id="u1", command_id="c1",
        approval_request_id="req-1", exit_code=0, succeeded=True,
    ))
    by_command = repo.query(command_id="c1")
    assert len(by_command) == 3
    types = {type(e).__name__ for e in by_command}
    assert types == {"CommandPolicyEvaluated", "CommandApprovalDecision", "CommandExecutionFinished"}


def test_mongo_audit_deletion_invariant(mongo_db):
    """MongoAuditRepository has no delete method."""
    from app.audit import MongoAuditRepository
    repo = MongoAuditRepository(mongo_db)
    assert not hasattr(repo, "delete")


def test_mongo_audit_query_filters(mongo_db):
    from app.audit import MongoAuditRepository
    from app.models import CommandPolicyEvaluated

    repo = MongoAuditRepository(mongo_db)
    repo.append(CommandPolicyEvaluated(
        run_id="r1", owner_id="alice", command_id="c1",
        argv=["ls"], allowed=True, approval_required=False,
    ))
    repo.append(CommandPolicyEvaluated(
        run_id="r2", owner_id="bob", command_id="c2",
        argv=["rm", "-rf", "/"], allowed=False, approval_required=False,
    ))
    assert len(repo.query(owner_id="alice")) == 1
    assert len(repo.query(run_id="r2")) == 1
    assert len(repo.query(event_type="command_policy_evaluated")) == 2
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_mongo_repos.py -k "audit" -v
```

**Step 3: Add `MongoAuditRepository` to `app/audit.py`**

```python
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
```

**Step 4: Run tests**

```bash
pytest tests/test_mongo_repos.py -v
```

**Step 5: Commit**

```bash
git add app/audit.py tests/test_mongo_repos.py
git commit -m "feat(audit): add MongoAuditRepository"
```

---

### Task 4: Wire `AuditRepository` into `AgentWorkflow`

**Files:**
- Modify: `app/workflow.py`
- Modify: `tests/test_workflow.py`

**Step 1: Understand existing workflow test fixtures**

Read `tests/test_workflow.py` before writing new tests to match existing mock fixture names.

**Step 2: Write failing tests**

```python
# tests/test_workflow.py — add to existing file
from unittest.mock import MagicMock
import uuid


def _make_audit_repo():
    from app.models import CommandPolicyEvaluated, CommandApprovalDecision, CommandExecutionFinished
    repo = MagicMock()
    repo.appended = []
    repo.append.side_effect = lambda e: repo.appended.append(e)
    return repo


def test_workflow_emits_policy_and_execution_events_on_allow(
    mock_planner, mock_policy, mock_executor
):
    from app.workflow import AgentWorkflow
    from app.models import CommandPolicyEvaluated, CommandExecutionFinished

    audit_repo = _make_audit_repo()
    wf = AgentWorkflow(
        planner=mock_planner,
        policy=mock_policy,
        executor=mock_executor,
        audit_repo=audit_repo,
    )
    wf.run(prompt="test", run_id="r1", max_iterations=1)

    types = [type(e).__name__ for e in audit_repo.appended]
    assert "CommandPolicyEvaluated" in types
    assert "CommandExecutionFinished" in types
    assert "CommandApprovalDecision" not in types

    policy_event = next(e for e in audit_repo.appended if isinstance(e, CommandPolicyEvaluated))
    exec_event = next(e for e in audit_repo.appended if isinstance(e, CommandExecutionFinished))
    assert policy_event.run_id == "r1"
    assert policy_event.allowed is True
    assert policy_event.command_id == exec_event.command_id


def test_workflow_emits_only_policy_event_on_policy_deny(
    mock_planner, mock_policy_deny, mock_executor
):
    from app.workflow import AgentWorkflow
    from app.models import CommandPolicyEvaluated

    audit_repo = _make_audit_repo()
    wf = AgentWorkflow(
        planner=mock_planner,
        policy=mock_policy_deny,
        executor=mock_executor,
        audit_repo=audit_repo,
    )
    wf.run(prompt="test", run_id="r1", max_iterations=1)

    types = [type(e).__name__ for e in audit_repo.appended]
    assert "CommandPolicyEvaluated" in types
    assert "CommandApprovalDecision" not in types
    assert "CommandExecutionFinished" not in types

    policy_event = next(e for e in audit_repo.appended if isinstance(e, CommandPolicyEvaluated))
    assert policy_event.allowed is False
```

**Step 3: Run to confirm they fail**

```bash
pytest tests/test_workflow.py -k "audit" -v
```

**Step 4: Update `app/workflow.py`**

1. Add `audit_repo` parameter to `__init__`:

```python
def __init__(
    self,
    planner: Planner,
    policy: PolicyEvaluator,
    executor: CommandExecutor,
    approval_gate: Callable[[Command], bool] | None = None,
    secrets_store: SecretsStore | None = None,
    audit_repo=None,
) -> None:
    ...
    self._audit_repo = audit_repo
```

2. In the `run()` loop, generate a `command_id` per iteration at the top of the VALIDATE block:

```python
import uuid as _uuid

# VALIDATE (OPA step)
command_id = str(_uuid.uuid4())
allowed, reason, skill = self._policy.evaluate(command, skill_overrides=ctx.skill_overrides)
approval_required = self._approval_gate is not None

self._emit_policy_event(ctx, command, command_id, skill, allowed, reason, approval_required)

if not allowed:
    ...
    break
```

3. After the approval gate block, emit an approval decision on denial:

```python
if self._approval_gate is not None:
    approval_id = str(_uuid.uuid4())
    approved = self._approval_gate(command)
    if not approved:
        self._emit_approval_event(ctx, command_id, approval_id,
            actor_id=getattr(ctx, "last_actor_id", None),
            decision="denied" if not getattr(ctx, "_approval_expired", False) else "expired")
        ctx.status = "policy_denied"
        ctx.final_message = f"Run stopped: '{ ' '.join(command.argv) }' was not approved."
        break
    self._emit_approval_event(ctx, command_id, approval_id,
        actor_id=getattr(ctx, "last_actor_id", None),
        decision="approved")
else:
    approval_id = None
```

4. After `executor.run()`, emit execution event:

```python
result = self._executor.run(command, env=skill_env)
result.skill_name = skill.name if skill else None
ctx.history.append(result)
self._emit_execution_event(ctx, command_id, approval_id, result)
```

5. Add helper methods:

```python
def _emit_policy_event(self, ctx, command, command_id, skill, allowed, reason, approval_required) -> None:
    if self._audit_repo is None:
        return
    from app.models import CommandPolicyEvaluated
    try:
        self._audit_repo.append(CommandPolicyEvaluated(
            run_id=ctx.run_id,
            owner_id=ctx.owner_id or "",
            command_id=command_id,
            argv=command.argv,
            skill_name=skill.name if skill else None,
            allowed=allowed,
            policy_reason=reason,
            approval_required=approval_required,
        ))
    except Exception as exc:
        logger.warning("Failed to append policy audit event: %s", exc)


def _emit_approval_event(self, ctx, command_id, approval_id, actor_id, decision) -> None:
    if self._audit_repo is None:
        return
    from app.models import CommandApprovalDecision
    try:
        self._audit_repo.append(CommandApprovalDecision(
            run_id=ctx.run_id,
            owner_id=ctx.owner_id or "",
            command_id=command_id,
            approval_request_id=approval_id,
            actor_id=actor_id,
            decision=decision,
        ))
    except Exception as exc:
        logger.warning("Failed to append approval audit event: %s", exc)


def _emit_execution_event(self, ctx, command_id, approval_id, result) -> None:
    if self._audit_repo is None:
        return
    from app.models import CommandExecutionFinished
    try:
        self._audit_repo.append(CommandExecutionFinished(
            run_id=ctx.run_id,
            owner_id=ctx.owner_id or "",
            command_id=command_id,
            approval_request_id=approval_id,
            exit_code=result.exit_code if result.exit_code is not None else -1,
            succeeded=(result.exit_code == 0),
        ))
    except Exception as exc:
        logger.warning("Failed to append execution audit event: %s", exc)
```

**Step 5: Run tests**

```bash
pytest tests/test_workflow.py -v
```

**Step 6: Commit**

```bash
git add app/workflow.py tests/test_workflow.py
git commit -m "feat(audit): wire 3-event audit emission into AgentWorkflow"
```

---

### Task 5: Record `actor_id` on approval/deny + expiry

**Files:**
- Modify: `app/main.py`
- Modify: `app/models.py`

**Step 1: Add transient `last_actor_id` field to `RunContext`**

In `app/models.py`, add to `RunContext`:

```python
last_actor_id: str | None = None   # transient; not persisted (cleared after each command)
```

**Step 2: Update `PendingApproval` dataclass**

```python
@dataclass
class PendingApproval:
    run_id: str
    command: Command
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    actor_id: str | None = None   # set by approve/deny endpoint
    timed_out: bool = False       # set by gate on timeout
```

**Step 3: Set `actor_id` in `approve_command` and `deny_command` endpoints**

```python
# approve_command
approval.actor_id = current_user.user_id
approval.approved = True
approval.event.set()

# deny_command
approval.actor_id = current_user.user_id
approval.event.set()  # approved stays False
```

**Step 4: Propagate `actor_id` and expiry flag via `ctx` in the gate**

In `_make_approval_gate`, after `approval.event.wait()`:

```python
timed_out = not approval.event.wait(timeout=300)
ctx.last_actor_id = approval.actor_id
if timed_out:
    ctx._approval_expired = True
    ...
else:
    ctx._approval_expired = False
```

**Step 5: Run tests**

```bash
pytest tests/test_main_auth.py tests/test_main_rbac.py tests/test_workflow.py -v
```

**Step 6: Commit**

```bash
git add app/main.py app/models.py
git commit -m "feat(audit): record actor_id and expiry for approval events"
```

---

### Task 6: `app/config.py` — `audit_file` setting

**Files:**
- Modify: `app/config.py`
- Modify: `tests/test_config.py`

**Step 1: Write failing test**

```python
# tests/test_config.py — add alongside existing tests
def test_audit_file_default():
    from app.config import Settings
    s = Settings()
    assert str(s.audit_file) == "audit.jsonl"
```

**Step 2: Run to confirm it fails**

```bash
pytest tests/test_config.py::test_audit_file_default -v
```

**Step 3: Add to `app/config.py`**

```python
audit_file: Path = Path("./audit.jsonl")
```

**Step 4: Run tests**

```bash
pytest tests/test_config.py -v
```

**Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat(audit): add AUDIT_FILE config setting"
```

---

### Task 7: Wire `AuditRepository` into lifespan + `_build_workflow`

**Files:**
- Modify: `app/main.py`
- Modify: `tests/conftest.py`

**Step 1: In `lifespan`, instantiate the correct repo**

After the existing backend-selection block, add:

```python
if settings.mongodb_uri:
    from app.audit import MongoAuditRepository
    audit_repo = MongoAuditRepository(mongo_db)
else:
    from app.audit import FileAuditRepository
    audit_repo = FileAuditRepository(settings.audit_file)

app.state.audit_repo = audit_repo
```

**Step 2: Pass `audit_repo` through `_build_workflow`**

```python
def _build_workflow(
    skill_repo,
    run_id=None, ctx=None,
    remote_skill_repo=None,
    secrets_store=None,
    user_require_approval=False,
    audit_repo=None,
) -> AgentWorkflow:
    ...
    return AgentWorkflow(
        planner=..., policy=..., executor=...,
        approval_gate=gate,
        secrets_store=secrets_store,
        audit_repo=audit_repo,
    )
```

Update the call site in `_start_run_internal`:

```python
workflow = _build_workflow(
    skill_repo,
    run_id=run_id, ctx=ctx,
    remote_skill_repo=remote_skill_repo,
    secrets_store=app.state.secrets_store,
    user_require_approval=current_user.require_approval,
    audit_repo=getattr(app.state, "audit_repo", None),
)
```

**Step 3: Wire `audit_repo` into the `client` test fixture**

```python
# tests/conftest.py — inside client fixture after other state assignments
from app.audit import FileAuditRepository
app.state.audit_repo = FileAuditRepository(tmp_path / "audit.jsonl")
```

**Step 4: Run the full test suite**

```bash
pytest -v
```

Expected: all tests pass.

**Step 5: Commit**

```bash
git add app/main.py tests/conftest.py
git commit -m "feat(audit): wire AuditRepository into lifespan and workflow builder"
```

---

### Task 8: `GET /api/admin/audit` endpoint

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_audit_api.py`

**Step 1: Write failing tests**

```python
# tests/test_audit_api.py
import pytest
from app.main import app
from app.models import CommandPolicyEvaluated, CommandExecutionFinished
from app.audit import FileAuditRepository


@pytest.fixture
def audit_client(client, tmp_path):
    """client fixture with a pre-populated audit repo."""
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    app.state.audit_repo = repo

    e1 = CommandPolicyEvaluated(
        run_id="r1", owner_id="u1", command_id="c1",
        argv=["ls"], allowed=True, approval_required=False, skill_name="shell",
    )
    e2 = CommandPolicyEvaluated(
        run_id="r2", owner_id="u2", command_id="c2",
        argv=["rm", "-rf", "/"], allowed=False, approval_required=False,
    )
    repo.append(e1)
    repo.append(e2)
    return client


def test_admin_can_list_audit_events(audit_client):
    resp = audit_client.get("/api/admin/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_audit_filter_by_event_type(audit_client):
    resp = audit_client.get("/api/admin/audit?event_type=command_policy_evaluated")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_audit_filter_by_run_id(audit_client):
    resp = audit_client.get("/api/admin/audit?run_id=r1")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_audit_filter_by_skill_name(audit_client):
    resp = audit_client.get("/api/admin/audit?skill_name=shell")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_audit_pagination(audit_client):
    resp = audit_client.get("/api/admin/audit?limit=1&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    assert resp.json()["total"] == 2


def test_non_admin_gets_403(user_client):
    resp = user_client.get("/api/admin/audit")
    assert resp.status_code == 403


def test_run_delete_does_not_remove_audit_events(audit_client, tmp_path):
    """Deleting a run must not touch the audit log."""
    import app.main as main_module
    from app.runs import RunRepository
    from app.models import RunContext

    run_repo = RunRepository(tmp_path / "runs_del.json")
    app.state.run_repo = run_repo
    ctx = RunContext(run_id="r1", prompt="test", owner_id="test-admin-id")
    run_repo.save(ctx)
    with main_module._runs_lock:
        main_module._runs["r1"] = ctx

    audit_client.delete("/api/agent/runs/r1")

    # Audit events for r1 still present
    repo: FileAuditRepository = app.state.audit_repo
    events = repo.query(run_id="r1")
    assert len(events) == 1  # the e1 event created in the fixture for r1
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_audit_api.py -v
```

**Step 3: Add endpoint to `app/main.py`**

```python
class AuditQueryResponse(BaseModel):
    total: int
    items: list[dict]


@app.get("/api/admin/audit", response_model=AuditQueryResponse)
def list_audit_events(
    request: Request,
    current_user: User = Depends(require_admin),
    run_id: str | None = None,
    owner_id: str | None = None,
    skill_name: str | None = None,
    event_type: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> AuditQueryResponse:
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if audit_repo is None:
        return AuditQueryResponse(total=0, items=[])

    from_ts = datetime.fromisoformat(from_) if from_ else None
    to_ts = datetime.fromisoformat(to) if to else None
    limit = min(limit, 1000)

    # skill_name is only on CommandPolicyEvaluated; filter post-query
    events = audit_repo.query(
        run_id=run_id,
        owner_id=owner_id,
        event_type=event_type,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=100_000,
        offset=0,
    )
    if skill_name is not None:
        events = [e for e in events if getattr(e, "skill_name", None) == skill_name]

    total = len(events)
    page = events[offset: offset + limit]
    return AuditQueryResponse(
        total=total,
        items=[{**e.model_dump(mode="json"), "event_type": _event_type_tag_for_api(e)} for e in page],
    )
```

Add a small helper (import from `app.audit` or inline):

```python
from app.audit import _event_type_tag as _event_type_tag_for_api
```

Also add `Query` to the FastAPI imports at the top of `app/main.py`:
```python
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
```

**Step 4: Run tests**

```bash
pytest tests/test_audit_api.py -v
```

**Step 5: Run full suite**

```bash
pytest -v
```

Expected: all tests pass.

**Step 6: Commit**

```bash
git add app/main.py tests/test_audit_api.py
git commit -m "feat(audit): add GET /api/admin/audit endpoint"
```

---

### Task 9: Update README

**Files:**
- Modify: `README.md`

**Step 1: Update the comparison table**

Add a new row after the existing `Audit trail` row:

```markdown
| Audit logging (immutable, separate store) | ✗ | ✓ survives run deletion, append-only |
```

**Step 2: Replace the "Audit trail" subsection** under Security model

Replace:
```
Every run is persisted with its full command history, timestamps, originating prompt, and final status. The run history is browsable via the UI and queryable via the API.
```

With:

```
Every command execution attempt produces a chain of immutable audit events persisted to a **separate** store — never deleted when a run is deleted.

Three linked event types share `run_id` and `command_id`:

| Event | When emitted |
|---|---|
| `command_policy_evaluated` | Always — records argv (raw, with `${VAR}` placeholders), skill, OPA result, whether approval is required |
| `command_approval_decision` | Only when the approval gate fires — records actor, decision (`approved`/`denied`/`expired`), timestamp |
| `command_execution_finished` | Only when the command actually ran — records exit code and success flag |

Storage backends:
- **File backend**: `audit.jsonl` (append-only, one JSON object per line). Path configurable via `AUDIT_FILE`. The file is never rewritten or truncated.
- **MongoDB backend**: `audit_events` collection with no TTL index — records are permanent.

Admins can query audit events via `GET /api/admin/audit` with filters for `run_id`, `owner_id`, `skill_name`, `event_type`, and time range.
```

**Step 3: Add `AUDIT_FILE` to the configuration table**

```markdown
| `AUDIT_FILE` | no | `./audit.jsonl` | Path to append-only audit log (file backend only) |
```

**Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document 3-event audit logging in README"
```

---

### Final verification

```bash
pytest -v
```

All tests should pass. Also verify `audit.jsonl` is in `.gitignore`.
