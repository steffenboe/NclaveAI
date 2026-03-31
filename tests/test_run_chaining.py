from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models import ActionResult, Command, RunContext
import app.main as main_module


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clear_runs():
    main_module._runs.clear()
    yield
    main_module._runs.clear()


def _fake_workflow():
    """Returns a mock AgentWorkflow whose run() sets status=done and returns ctx."""
    wf = MagicMock()
    def fake_run(prompt, max_iterations, ctx):
        ctx.status = "done"
        ctx.final_message = "ok"
        return ctx
    wf.run.side_effect = fake_run
    return wf


def test_start_run_accepts_context_run_id(client):
    """POST /api/agent/run with context_run_id returns 202."""
    parent = RunContext(run_id="parent-1", prompt="first prompt", status="done")
    main_module._runs["parent-1"] = parent

    with patch("app.main._build_workflow", return_value=_fake_workflow()):
        resp = client.post("/api/agent/run", json={
            "prompt": "follow up",
            "context_run_id": "parent-1",
        })

    assert resp.status_code == 202
    assert resp.json()["status"] == "running"


def test_new_run_has_parent_run_id_set(client):
    """New run created with context_run_id has parent_run_id pointing to the context run."""
    parent = RunContext(run_id="parent-1", prompt="first prompt", status="done")
    main_module._runs["parent-1"] = parent

    with patch("app.main._build_workflow", return_value=_fake_workflow()):
        resp = client.post("/api/agent/run", json={
            "prompt": "follow up",
            "context_run_id": "parent-1",
        })

    new_run_id = resp.json()["run_id"]
    time.sleep(0.05)
    ctx = main_module._runs[new_run_id]
    assert ctx.parent_run_id == "parent-1"


def test_new_run_seeded_with_parent_history(client):
    """New run starts with a copy of the parent's history."""
    cmd = Command(argv=["ls", "/"], rationale="list root")
    action = ActionResult(command=cmd, allowed=True, exit_code=0, stdout="bin etc")
    parent = RunContext(
        run_id="parent-1",
        prompt="first prompt",
        status="done",
        history=[action],
    )
    main_module._runs["parent-1"] = parent

    captured = {}

    def capturing_run(prompt, max_iterations, ctx):
        captured["history_len"] = len(ctx.history)
        ctx.status = "done"
        return ctx

    wf = MagicMock()
    wf.run.side_effect = capturing_run

    with patch("app.main._build_workflow", return_value=wf):
        client.post("/api/agent/run", json={
            "prompt": "follow up",
            "context_run_id": "parent-1",
        })

    time.sleep(0.1)
    assert captured.get("history_len") == 1


def test_start_run_without_context_run_id_works_normally(client):
    """POST without context_run_id behaves as before (no parent_run_id)."""
    with patch("app.main._build_workflow", return_value=_fake_workflow()):
        resp = client.post("/api/agent/run", json={"prompt": "fresh start"})

    assert resp.status_code == 202
    new_run_id = resp.json()["run_id"]
    time.sleep(0.05)
    ctx = main_module._runs[new_run_id]
    assert ctx.parent_run_id is None


def test_new_run_inherits_parent_skill_overrides(client):
    """New run started from a parent with skill_overrides copies those overrides."""
    parent = RunContext(
        run_id="parent-sk",
        prompt="first",
        status="done",
        skill_overrides={"skill-abc": False, "skill-xyz": True},
    )
    main_module._runs["parent-sk"] = parent

    with patch("app.main._build_workflow", return_value=_fake_workflow()):
        resp = client.post("/api/agent/run", json={
            "prompt": "follow up",
            "context_run_id": "parent-sk",
        })

    new_run_id = resp.json()["run_id"]
    time.sleep(0.1)
    ctx = main_module._runs[new_run_id]
    assert ctx.skill_overrides == {"skill-abc": False, "skill-xyz": True}


def test_new_run_without_parent_starts_with_empty_skill_overrides(client):
    """A fresh run (no context_run_id) starts with no skill_overrides."""
    with patch("app.main._build_workflow", return_value=_fake_workflow()):
        resp = client.post("/api/agent/run", json={"prompt": "fresh"})

    new_run_id = resp.json()["run_id"]
    time.sleep(0.05)
    ctx = main_module._runs[new_run_id]
    assert ctx.skill_overrides == {}
