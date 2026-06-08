# tests/test_audit_api.py
import pytest
from app.models import CommandPolicyEvaluated, CommandExecutionFinished
from app.audit import FileAuditRepository


@pytest.fixture
def audit_client(admin_client, tmp_path):
    """admin_client fixture with a pre-populated audit repo."""
    repo = FileAuditRepository(tmp_path / "audit.jsonl")
    admin_client.app.state.audit_repo = repo

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
    return admin_client


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
    audit_client.app.state.run_repo = run_repo
    ctx = RunContext(run_id="r1", prompt="test", owner_id="test-admin-id")
    run_repo.save(ctx)
    with main_module._runs_lock:
        main_module._runs["r1"] = ctx

    audit_client.delete("/api/agent/runs/r1")

    # Audit events for r1 still present
    repo: FileAuditRepository = audit_client.app.state.audit_repo
    events = repo.query(run_id="r1")
    assert len(events) == 1  # the e1 event created in the fixture for r1
