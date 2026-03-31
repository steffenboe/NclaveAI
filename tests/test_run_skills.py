from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app, _runs, _runs_lock
from app.models import RunContext
from app.skills import SkillRepository


@pytest.fixture(autouse=True)
def clear_runs():
    """Ensure _runs is empty before each test."""
    with _runs_lock:
        _runs.clear()
    yield
    with _runs_lock:
        _runs.clear()


@pytest.fixture
def client(tmp_path):
    repo = SkillRepository(tmp_path / "skills.json")
    app.state.skill_repo = repo
    return TestClient(app)


@pytest.fixture
def skill_and_run(client):
    """Create one skill and one run, returning (skill_id, run_id)."""
    skill_res = client.post("/api/skills", json={"name": "kubectl", "description": "k8s cli"})
    assert skill_res.status_code == 201
    skill_id = skill_res.json()["id"]

    ctx = RunContext(run_id="test-run-1", prompt="hello")
    with _runs_lock:
        _runs["test-run-1"] = ctx

    return skill_id, "test-run-1"


# ── GET /api/agent/runs/{run_id}/skills ───────────────────────────────────────

def test_get_run_skills_returns_skill_list(client, skill_and_run):
    skill_id, run_id = skill_and_run
    res = client.get(f"/api/agent/runs/{run_id}/skills")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == skill_id
    assert data[0]["name"] == "kubectl"


def test_get_run_skills_includes_effective_enabled(client, skill_and_run):
    skill_id, run_id = skill_and_run
    res = client.get(f"/api/agent/runs/{run_id}/skills")
    assert res.status_code == 200
    item = res.json()[0]
    assert "effective_enabled" in item
    assert item["effective_enabled"] == item["enabled"]  # no override yet


def test_get_run_skills_reflects_override(client, skill_and_run):
    skill_id, run_id = skill_and_run
    # Apply override
    patch_res = client.patch(
        f"/api/agent/runs/{run_id}/skills/{skill_id}",
        json={"enabled": False},
    )
    assert patch_res.status_code == 200
    # Now GET should show effective_enabled=False even though global enabled=True
    res = client.get(f"/api/agent/runs/{run_id}/skills")
    assert res.status_code == 200
    item = res.json()[0]
    assert item["enabled"] is True          # global unchanged
    assert item["effective_enabled"] is False  # override applied


def test_get_run_skills_404_for_unknown_run(client):
    res = client.get("/api/agent/runs/nonexistent-run/skills")
    assert res.status_code == 404


# ── PATCH /api/agent/runs/{run_id}/skills/{skill_id} ─────────────────────────

def test_patch_run_skill_sets_override(client, skill_and_run):
    skill_id, run_id = skill_and_run
    res = client.patch(
        f"/api/agent/runs/{run_id}/skills/{skill_id}",
        json={"enabled": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == skill_id
    assert data["effective_enabled"] is False
    assert data["enabled"] is True  # global unchanged


def test_patch_run_skill_override_enables_disabled_skill(client, tmp_path):
    """Globally disabled skill can be enabled per-run."""
    repo = SkillRepository(tmp_path / "skills.json")
    app.state.skill_repo = repo
    c = TestClient(app)

    skill_res = c.post("/api/skills", json={"name": "gh", "description": "GitHub CLI", "enabled": False})
    skill_id = skill_res.json()["id"]

    ctx = RunContext(run_id="run-2", prompt="test")
    with _runs_lock:
        _runs["run-2"] = ctx

    res = c.patch(f"/api/agent/runs/run-2/skills/{skill_id}", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["effective_enabled"] is True
    assert res.json()["enabled"] is False  # global still False


def test_patch_run_skill_404_for_unknown_run(client, skill_and_run):
    skill_id, _ = skill_and_run
    res = client.patch(f"/api/agent/runs/no-such-run/skills/{skill_id}", json={"enabled": False})
    assert res.status_code == 404


def test_patch_run_skill_404_for_unknown_skill(client, skill_and_run):
    _, run_id = skill_and_run
    res = client.patch(f"/api/agent/runs/{run_id}/skills/no-such-skill", json={"enabled": False})
    assert res.status_code == 404


def test_patch_run_skill_persists_in_run_context(client, skill_and_run):
    skill_id, run_id = skill_and_run
    client.patch(f"/api/agent/runs/{run_id}/skills/{skill_id}", json={"enabled": False})
    with _runs_lock:
        ctx = _runs[run_id]
    assert ctx.skill_overrides[skill_id] is False
