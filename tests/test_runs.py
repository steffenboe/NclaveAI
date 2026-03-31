from __future__ import annotations

import json
import logging

import pytest

from app.runs import RunRepository
from app.models import RunContext


@pytest.fixture
def runs_file(tmp_path):
    return tmp_path / "runs.json"


@pytest.fixture
def repo(runs_file):
    return RunRepository(runs_file)


def _make_run(run_id="r1", status="done", final_message="ok") -> RunContext:
    return RunContext(run_id=run_id, prompt="test", status=status, final_message=final_message)


# ── Startup behaviour ─────────────────────────────────────────────────────────

def test_starts_empty_when_file_missing(runs_file):
    repo = RunRepository(runs_file)
    assert repo.list() == []


def test_missing_file_logs_warning(runs_file, caplog):
    with caplog.at_level(logging.WARNING, logger="app.runs"):
        RunRepository(runs_file)
    assert "runs.json" in caplog.text.lower() or "runs" in caplog.text.lower()


def test_corrupt_file_raises_value_error(tmp_path):
    bad = tmp_path / "runs.json"
    bad.write_text("not json {{{")
    with pytest.raises(ValueError, match="not valid JSON"):
        RunRepository(bad)


# ── save / list / get ─────────────────────────────────────────────────────────

def test_save_and_get(repo):
    ctx = _make_run()
    repo.save(ctx)
    fetched = repo.get("r1")
    assert fetched.run_id == "r1"
    assert fetched.status == "done"


def test_save_persists_to_file(repo, runs_file):
    repo.save(_make_run())
    data = json.loads(runs_file.read_text())
    assert len(data) == 1
    assert data[0]["run_id"] == "r1"


def test_list_returns_all_saved_runs(repo):
    repo.save(_make_run("a"))
    repo.save(_make_run("b"))
    ids = [r.run_id for r in repo.list()]
    assert sorted(ids) == ["a", "b"]


def test_get_unknown_raises_key_error(repo):
    with pytest.raises(KeyError):
        repo.get("nonexistent")


# ── JSON round-trip ───────────────────────────────────────────────────────────

def test_json_round_trip(tmp_path):
    path = tmp_path / "runs.json"
    repo1 = RunRepository(path)
    ctx = _make_run(status="done", final_message="all good")
    repo1.save(ctx)

    repo2 = RunRepository(path)
    loaded = repo2.get("r1")
    assert loaded.status == "done"
    assert loaded.final_message == "all good"
    assert loaded.prompt == "test"


# ── pending_command never persisted ──────────────────────────────────────────

def test_pending_command_not_persisted(tmp_path):
    path = tmp_path / "runs.json"
    repo1 = RunRepository(path)
    ctx = _make_run()
    ctx.pending_command = {"argv": ["ls"], "rationale": "check"}
    repo1.save(ctx)

    data = json.loads(path.read_text())
    assert data[0]["pending_command"] is None


# ── Restart recovery ──────────────────────────────────────────────────────────

def test_restart_recovery_running_to_failed(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps([{
        "run_id": "r1", "prompt": "p", "history": [],
        "status": "running", "final_message": None,
        "pending_command": None, "parent_run_id": None,
        "skill_overrides": {},
    }]))
    repo = RunRepository(path)
    ctx = repo.get("r1")
    assert ctx.status == "failed"
    assert ctx.final_message == "Server restarted."


def test_restart_recovery_waiting_approval_to_failed(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps([{
        "run_id": "r1", "prompt": "p", "history": [],
        "status": "waiting_approval", "final_message": None,
        "pending_command": {"argv": ["ls"], "rationale": "x"},
        "parent_run_id": None, "skill_overrides": {},
    }]))
    repo = RunRepository(path)
    ctx = repo.get("r1")
    assert ctx.status == "failed"
    assert ctx.final_message == "Server restarted."
    assert ctx.pending_command is None


def test_terminal_runs_survive_restart(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps([{
        "run_id": "r1", "prompt": "p", "history": [],
        "status": "done", "final_message": "great",
        "pending_command": None, "parent_run_id": None,
        "skill_overrides": {},
    }]))
    repo = RunRepository(path)
    ctx = repo.get("r1")
    assert ctx.status == "done"
    assert ctx.final_message == "great"


def test_all_as_dict_returns_copies(repo):
    ctx = _make_run()
    repo.save(ctx)
    d = repo.all_as_dict()
    assert "r1" in d
    # Mutating the returned object should not affect the repo
    d["r1"].status = "failed"
    assert repo.get("r1").status == "done"
