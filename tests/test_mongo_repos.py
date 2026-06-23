"""Tests for MongoDB-backed repository implementations.

Uses mongomock so no running MongoDB instance is required.
"""
from __future__ import annotations

import pytest
import mongomock

from datetime import datetime, timezone

from app.models import RunContext, ScheduledTask
from app.mongo_repos import (
    MongoAppSettingsRepository,
    MongoRunRepository,
    MongoScheduledTaskRepository,
    MongoSkillRepository,
    MongoUserRepository,
)
from app.settings_store import AppSettings


@pytest.fixture
def mongo_db():
    client = mongomock.MongoClient()
    return client["test-db"]


# ---------------------------------------------------------------------------
# MongoRunRepository
# ---------------------------------------------------------------------------

class TestMongoRunRepository:
    def test_save_and_get(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        ctx = RunContext(run_id="r1", prompt="hello", owner_id="u1")
        repo.save(ctx)
        loaded = repo.get("r1")
        assert loaded.run_id == "r1"
        assert loaded.prompt == "hello"

    def test_get_raises_key_error_when_missing(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        with pytest.raises(KeyError):
            repo.get("does-not-exist")

    def test_get_raises_key_error_wrong_owner(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        ctx = RunContext(run_id="r1", prompt="hello", owner_id="u1")
        repo.save(ctx)
        with pytest.raises(KeyError):
            repo.get("r1", owner_id="other")

    def test_list_empty(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        assert repo.list() == []

    def test_list_filters_by_owner(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        repo.save(RunContext(run_id="r1", prompt="a", owner_id="u1"))
        repo.save(RunContext(run_id="r2", prompt="b", owner_id="u2"))
        result = repo.list(owner_id="u1")
        assert len(result) == 1
        assert result[0].run_id == "r1"

    def test_delete(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        repo.save(RunContext(run_id="r1", prompt="hello"))
        repo.delete("r1")
        with pytest.raises(KeyError):
            repo.get("r1")

    def test_delete_raises_key_error_when_missing(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        with pytest.raises(KeyError):
            repo.delete("does-not-exist")

    def test_save_is_upsert(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        ctx = RunContext(run_id="r1", prompt="original")
        repo.save(ctx)
        updated = ctx.model_copy(update={"prompt": "updated"})
        repo.save(updated)
        assert repo.get("r1").prompt == "updated"
        assert len(repo.list()) == 1

    def test_all_as_dict(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        repo.save(RunContext(run_id="r1", prompt="a"))
        repo.save(RunContext(run_id="r2", prompt="b"))
        d = repo.all_as_dict()
        assert set(d.keys()) == {"r1", "r2"}

    def test_search(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        repo.save(RunContext(run_id="r1", prompt="deploy the app"))
        repo.save(RunContext(run_id="r2", prompt="list pods"))
        results = repo.search("deploy")
        assert len(results) == 1
        assert results[0].run_id == "r1"

    def test_pending_command_not_persisted(self, mongo_db):
        repo = MongoRunRepository(mongo_db)
        ctx = RunContext(run_id="r1", prompt="hello")
        ctx.pending_command = {"argv": ["ls"], "rationale": "check"}
        repo.save(ctx)
        loaded = repo.get("r1")
        assert loaded.pending_command is None

    def test_recover_in_flight_on_init(self, mongo_db):
        # Pre-populate collection with in-flight runs before creating repo
        mongo_db["runs"].insert_many([
            {"_id": "r1", "run_id": "r1", "prompt": "a", "status": "running",
             "history": [], "history_start_index": 0},
            {"_id": "r2", "run_id": "r2", "prompt": "b", "status": "waiting_approval",
             "history": [], "history_start_index": 0},
            {"_id": "r3", "run_id": "r3", "prompt": "c", "status": "done",
             "history": [], "history_start_index": 0},
        ])
        repo = MongoRunRepository(mongo_db)
        assert repo.get("r1").status == "failed"
        assert repo.get("r2").status == "failed"
        assert repo.get("r3").status == "done"


# ---------------------------------------------------------------------------
# MongoScheduledTaskRepository
# ---------------------------------------------------------------------------

class TestMongoScheduledTaskRepository:
    def _task(self, task_id: str = "t1", owner_id: str = "u1") -> ScheduledTask:
        now = datetime.now(timezone.utc)
        return ScheduledTask(
            task_id=task_id,
            owner_id=owner_id,
            prompt="p",
            cron="*/5 * * * *",
            timezone="UTC",
            enabled=True,
            created_at=now,
            updated_at=now,
        )

    def test_save_and_get(self, mongo_db):
        repo = MongoScheduledTaskRepository(mongo_db)
        repo.save(self._task("t1"))
        loaded = repo.get("t1")
        assert loaded.task_id == "t1"

    def test_get_raises_key_error_when_missing(self, mongo_db):
        repo = MongoScheduledTaskRepository(mongo_db)
        with pytest.raises(KeyError):
            repo.get("does-not-exist")

    def test_list_filters_by_owner(self, mongo_db):
        repo = MongoScheduledTaskRepository(mongo_db)
        repo.save(self._task("t1", owner_id="u1"))
        repo.save(self._task("t2", owner_id="u2"))
        result = repo.list(owner_id="u1")
        assert [task.task_id for task in result] == ["t1"]

    def test_delete(self, mongo_db):
        repo = MongoScheduledTaskRepository(mongo_db)
        repo.save(self._task("t1"))
        repo.delete("t1")
        with pytest.raises(KeyError):
            repo.get("t1")


# ---------------------------------------------------------------------------
# MongoUserRepository
# ---------------------------------------------------------------------------

class TestMongoUserRepository:
    def test_create_and_get(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        user = repo.create("alice", "hashed", "admin")
        assert user.username == "alice"
        loaded = repo.get(user.user_id)
        assert loaded is not None
        assert loaded.username == "alice"

    def test_create_duplicate_raises(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        repo.create("alice", "hashed", "admin")
        with pytest.raises(ValueError, match="already exists"):
            repo.create("alice", "hashed", "user")

    def test_get_returns_none_when_missing(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        assert repo.get("no-such-id") is None

    def test_get_by_username(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        repo.create("bob", "hashed", "user")
        user = repo.get_by_username("bob")
        assert user is not None
        assert user.username == "bob"

    def test_get_by_username_returns_none_when_missing(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        assert repo.get_by_username("nobody") is None

    def test_list(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        repo.create("alice", "h", "admin")
        repo.create("bob", "h", "user")
        assert len(repo.list()) == 2

    def test_update(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        user = repo.create("alice", "hashed", "user")
        updated = repo.update(user.user_id, role="admin")
        assert updated.role == "admin"

    def test_update_raises_key_error_when_missing(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        with pytest.raises(KeyError):
            repo.update("no-such-id", role="admin")

    def test_delete(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        user = repo.create("alice", "h", "admin")
        repo.delete(user.user_id)
        assert repo.get(user.user_id) is None

    def test_delete_raises_key_error_when_missing(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        with pytest.raises(KeyError):
            repo.delete("no-such-id")

    def test_count(self, mongo_db):
        repo = MongoUserRepository(mongo_db)
        assert repo.count() == 0
        repo.create("alice", "h", "admin")
        assert repo.count() == 1


# ---------------------------------------------------------------------------
# MongoSkillRepository
# ---------------------------------------------------------------------------

class TestMongoSkillRepository:
    def test_create_and_get(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        skill = repo.create("kubectl", "k8s CLI")
        assert skill.name == "kubectl"
        loaded = repo.get(skill.id)
        assert loaded.name == "kubectl"

    def test_get_raises_key_error_when_missing(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        with pytest.raises(KeyError):
            repo.get("no-such-id")

    def test_list_empty(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        assert repo.list() == []

    def test_list_ordered_by_created_at(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        a = repo.create("a", "first")
        b = repo.create("b", "second")
        names = [s.name for s in repo.list()]
        assert names == ["a", "b"]

    def test_update_name(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        skill = repo.create("old", "desc")
        updated = repo.update(skill.id, name="new")
        assert updated.name == "new"
        assert repo.get(skill.id).name == "new"

    def test_update_policy_to_none(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        skill = repo.create("s", "d", policy="allow")
        updated = repo.update(skill.id, policy=None)
        assert updated.policy is None

    def test_create_with_team_id(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        skill = repo.create("team-skill", "desc", team_id="team-1")
        assert skill.team_id == "team-1"
        assert repo.get(skill.id).team_id == "team-1"

    def test_update_team_id(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        skill = repo.create("s", "d")
        updated = repo.update(skill.id, team_id="team-1")
        assert updated.team_id == "team-1"
        updated = repo.update(skill.id, team_id=None)
        assert updated.team_id is None

    def test_update_raises_key_error_when_missing(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        with pytest.raises(KeyError):
            repo.update("no-such-id", name="x")

    def test_delete(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        skill = repo.create("s", "d")
        repo.delete(skill.id)
        with pytest.raises(KeyError):
            repo.get(skill.id)

    def test_delete_raises_key_error_when_missing(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        with pytest.raises(KeyError):
            repo.delete("no-such-id")

    def test_source_field_not_persisted(self, mongo_db):
        repo = MongoSkillRepository(mongo_db)
        skill = repo.create("s", "d")
        doc = mongo_db["skills"].find_one({"_id": skill.id})
        assert "source" not in doc


# ---------------------------------------------------------------------------
# MongoAppSettingsRepository
# ---------------------------------------------------------------------------

class TestMongoAppSettingsRepository:
    def test_load_returns_defaults_when_empty(self, mongo_db):
        repo = MongoAppSettingsRepository(mongo_db)
        s = repo.load()
        assert s.skills_repo_url is None
        assert s.skills_repo_branch == "main"

    def test_save_and_load_round_trip(self, mongo_db):
        repo = MongoAppSettingsRepository(mongo_db)
        repo.save(AppSettings(skills_repo_url="https://example.com/repo", skills_repo_branch="develop"))
        loaded = repo.load()
        assert loaded.skills_repo_url == "https://example.com/repo"
        assert loaded.skills_repo_branch == "develop"

    def test_save_null_url(self, mongo_db):
        repo = MongoAppSettingsRepository(mongo_db)
        repo.save(AppSettings(skills_repo_url=None))
        loaded = repo.load()
        assert loaded.skills_repo_url is None

    def test_save_is_upsert(self, mongo_db):
        repo = MongoAppSettingsRepository(mongo_db)
        repo.save(AppSettings(skills_repo_url="https://first.example.com"))
        repo.save(AppSettings(skills_repo_url="https://second.example.com"))
        assert repo.load().skills_repo_url == "https://second.example.com"
        assert mongo_db["settings"].count_documents({}) == 1


# ---------------------------------------------------------------------------
# MongoAuditRepository
# ---------------------------------------------------------------------------

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

