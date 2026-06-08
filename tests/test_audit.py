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
    
    # Manually serialize with event_type field
    from app.audit import _serialize
    path.write_text(_serialize(old) + "\n" + _serialize(recent) + "\n")
    
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
