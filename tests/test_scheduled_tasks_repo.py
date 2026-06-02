from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import ScheduledTask
from app.scheduled_tasks import ScheduledTaskRepository


@pytest.fixture
def repo(tmp_path):
    return ScheduledTaskRepository(tmp_path / "scheduled_tasks.json")


def _make_task(task_id: str = "t1", owner_id: str = "u1") -> ScheduledTask:
    now = datetime.now(timezone.utc)
    return ScheduledTask(
        task_id=task_id,
        owner_id=owner_id,
        prompt="hello",
        cron="*/5 * * * *",
        timezone="UTC",
        enabled=True,
        created_at=now,
        updated_at=now,
    )


def test_save_and_get(repo):
    task = _make_task()
    repo.save(task)
    loaded = repo.get("t1")
    assert loaded.task_id == "t1"
    assert loaded.prompt == "hello"


def test_get_missing_raises(repo):
    with pytest.raises(KeyError):
        repo.get("missing")


def test_owner_filtering(repo):
    repo.save(_make_task("t1", owner_id="u1"))
    repo.save(_make_task("t2", owner_id="u2"))
    mine = repo.list(owner_id="u1")
    assert [t.task_id for t in mine] == ["t1"]


def test_delete(repo):
    repo.save(_make_task("t1"))
    repo.delete("t1")
    with pytest.raises(KeyError):
        repo.get("t1")


def test_all_as_dict_returns_copy(repo):
    repo.save(_make_task("t1"))
    snapshot = repo.all_as_dict()
    snapshot["t1"].prompt = "changed"
    assert repo.get("t1").prompt == "hello"
