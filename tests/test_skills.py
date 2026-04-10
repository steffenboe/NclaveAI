from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.skills import Skill, SkillRepository


@pytest.fixture
def skills_file(tmp_path):
    return tmp_path / "skills.json"


@pytest.fixture
def repo(skills_file):
    return SkillRepository(skills_file)


# ── Startup behaviour ─────────────────────────────────────────────────────────

def test_starts_empty_when_file_missing(skills_file):
    repo = SkillRepository(skills_file)
    assert repo.list() == []


def test_missing_file_logs_warning(skills_file, caplog):
    with caplog.at_level(logging.WARNING, logger="app.skills"):
        SkillRepository(skills_file)
    assert "skills.json" in caplog.text.lower() or "skills" in caplog.text.lower()


def test_corrupt_file_raises_value_error(tmp_path):
    bad = tmp_path / "skills.json"
    bad.write_text("not json {{{")
    with pytest.raises(ValueError, match="not valid JSON"):
        SkillRepository(bad)


# ── create ────────────────────────────────────────────────────────────────────

def test_create_returns_skill_with_all_fields(repo):
    skill = repo.create(name="kubectl", description="Kubernetes CLI")
    assert skill.name == "kubectl"
    assert skill.description == "Kubernetes CLI"
    assert skill.enabled is True
    assert skill.id != ""
    assert skill.created_at is not None


def test_create_enabled_false(repo):
    skill = repo.create(name="gh", description="GitHub CLI", enabled=False)
    assert skill.enabled is False


def test_create_persists_to_file(repo, skills_file):
    repo.create(name="gh", description="GitHub CLI")
    data = json.loads(skills_file.read_text())
    assert len(data) == 1
    assert data[0]["name"] == "gh"


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_returns_in_insertion_order(repo):
    repo.create(name="a", description="first")
    repo.create(name="b", description="second")
    names = [s.name for s in repo.list()]
    assert names == ["a", "b"]


def test_list_returns_copy(repo):
    repo.create(name="gh", description="desc")
    lst = repo.list()
    lst.clear()
    assert len(repo.list()) == 1  # original unaffected


# ── get ───────────────────────────────────────────────────────────────────────

def test_get_returns_skill(repo):
    skill = repo.create(name="terraform", description="IaC tool")
    fetched = repo.get(skill.id)
    assert fetched.id == skill.id
    assert fetched.name == "terraform"


def test_get_unknown_id_raises_key_error(repo):
    with pytest.raises(KeyError):
        repo.get("nonexistent-id")


# ── update ────────────────────────────────────────────────────────────────────

def test_update_name(repo):
    skill = repo.create(name="old", description="desc")
    updated = repo.update(skill.id, name="new")
    assert updated.name == "new"
    assert updated.description == "desc"  # unchanged


def test_update_description(repo):
    skill = repo.create(name="gh", description="old desc")
    updated = repo.update(skill.id, description="new desc")
    assert updated.description == "new desc"
    assert updated.name == "gh"  # unchanged


def test_update_enabled(repo):
    skill = repo.create(name="gh", description="desc", enabled=True)
    updated = repo.update(skill.id, enabled=False)
    assert updated.enabled is False


def test_update_does_not_mutate_id_or_created_at(repo):
    skill = repo.create(name="gh", description="desc")
    original_id = skill.id
    original_created_at = skill.created_at
    updated = repo.update(skill.id, name="renamed")
    assert updated.id == original_id
    assert updated.created_at == original_created_at


def test_update_persists_to_file(repo, skills_file):
    skill = repo.create(name="gh", description="desc")
    repo.update(skill.id, name="gh-cli")
    data = json.loads(skills_file.read_text())
    assert data[0]["name"] == "gh-cli"


def test_update_unknown_id_raises_key_error(repo):
    with pytest.raises(KeyError):
        repo.update("nonexistent", name="x")


def test_update_partial_only_changes_supplied_fields(repo):
    skill = repo.create(name="gh", description="desc", enabled=True)
    repo.update(skill.id, enabled=False)
    updated = repo.get(skill.id)
    assert updated.name == "gh"       # untouched
    assert updated.description == "desc"  # untouched
    assert updated.enabled is False


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_removes_skill(repo):
    skill = repo.create(name="gh", description="desc")
    repo.delete(skill.id)
    assert repo.list() == []


def test_delete_persists_to_file(repo, skills_file):
    skill = repo.create(name="gh", description="desc")
    repo.delete(skill.id)
    data = json.loads(skills_file.read_text())
    assert data == []


def test_delete_unknown_id_raises_key_error(repo):
    with pytest.raises(KeyError):
        repo.delete("nonexistent")


# ── JSON round-trip ───────────────────────────────────────────────────────────

def test_json_round_trip_preserves_all_fields(tmp_path):
    path = tmp_path / "skills.json"
    repo1 = SkillRepository(path)
    s = repo1.create(name="kubectl", description="k8s cli", enabled=False)

    repo2 = SkillRepository(path)
    loaded = repo2.get(s.id)
    assert loaded.name == "kubectl"
    assert loaded.description == "k8s cli"
    assert loaded.enabled is False
    assert loaded.created_at == s.created_at


# ── API endpoint tests ────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    repo = SkillRepository(tmp_path / "skills.json")
    app.state.skill_repo = repo
    return TestClient(app)


def test_api_list_skills_empty(client):
    res = client.get("/api/skills")
    assert res.status_code == 200
    assert res.json() == []


def test_api_create_skill(client):
    res = client.post("/api/skills", json={"name": "kubectl", "description": "k8s cli"})
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "kubectl"
    assert data["enabled"] is True
    assert "id" in data


def test_api_create_skill_enabled_false(client):
    res = client.post("/api/skills", json={"name": "gh", "description": "GitHub CLI", "enabled": False})
    assert res.status_code == 201
    assert res.json()["enabled"] is False


def test_api_create_skill_missing_name_returns_422(client):
    res = client.post("/api/skills", json={"description": "no name"})
    assert res.status_code == 422


def test_api_get_skill(client):
    created = client.post("/api/skills", json={"name": "gh", "description": "desc"}).json()
    res = client.get(f"/api/skills/{created['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == created["id"]


def test_api_get_skill_not_found(client):
    res = client.get("/api/skills/nonexistent-id")
    assert res.status_code == 404


def test_api_patch_skill_name(client):
    created = client.post("/api/skills", json={"name": "old", "description": "desc"}).json()
    res = client.patch(f"/api/skills/{created['id']}", json={"name": "new"})
    assert res.status_code == 200
    assert res.json()["name"] == "new"
    assert res.json()["description"] == "desc"  # unchanged


def test_api_patch_skill_enabled(client):
    created = client.post("/api/skills", json={"name": "gh", "description": "desc"}).json()
    res = client.patch(f"/api/skills/{created['id']}", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["enabled"] is False


def test_api_patch_skill_not_found(client):
    res = client.patch("/api/skills/nonexistent-id", json={"name": "x"})
    assert res.status_code == 404


def test_api_delete_skill(client):
    created = client.post("/api/skills", json={"name": "gh", "description": "desc"}).json()
    res = client.delete(f"/api/skills/{created['id']}")
    assert res.status_code == 204
    assert client.get("/api/skills").json() == []


def test_api_delete_skill_not_found(client):
    res = client.delete("/api/skills/nonexistent-id")
    assert res.status_code == 404


def test_api_list_reflects_created_skills(client):
    client.post("/api/skills", json={"name": "a", "description": "first"})
    client.post("/api/skills", json={"name": "b", "description": "second"})
    names = [s["name"] for s in client.get("/api/skills").json()]
    assert names == ["a", "b"]


# ── policy field ───────────────────────────────────────────────────────────────

def test_create_with_policy(repo):
    skill = repo.create(name="kubectl", description="k8s cli", policy='allow {\n  input.argv[0] == "kubectl"\n}')
    assert skill.policy == 'allow {\n  input.argv[0] == "kubectl"\n}'


def test_create_without_policy_defaults_to_none(repo):
    skill = repo.create(name="gh", description="GitHub CLI")
    assert skill.policy is None


def test_update_policy(repo):
    skill = repo.create(name="gh", description="desc")
    updated = repo.update(skill.id, policy='allow { true }')
    assert updated.policy == 'allow { true }'


def test_clear_policy_to_none(repo):
    skill = repo.create(name="gh", description="desc", policy='allow { true }')
    updated = repo.update(skill.id, policy=None)
    assert updated.policy is None


def test_update_omitting_policy_leaves_it_unchanged(repo):
    skill = repo.create(name="gh", description="desc", policy='allow { true }')
    updated = repo.update(skill.id, name="gh-cli")
    assert updated.policy == 'allow { true }'


def test_policy_round_trip(tmp_path):
    path = tmp_path / "skills.json"
    repo1 = SkillRepository(path)
    s = repo1.create(name="kubectl", description="k8s cli", policy='allow { true }')

    repo2 = SkillRepository(path)
    loaded = repo2.get(s.id)
    assert loaded.policy == 'allow { true }'


# ── API policy field ───────────────────────────────────────────────────────────

def test_api_create_skill_with_policy(client):
    res = client.post("/api/skills", json={
        "name": "kubectl",
        "description": "k8s cli",
        "policy": 'allow {\n  input.argv[0] == "kubectl"\n}',
    })
    assert res.status_code == 201
    data = res.json()
    assert data["policy"] == 'allow {\n  input.argv[0] == "kubectl"\n}'


def test_api_create_skill_default_policy_is_null(client):
    res = client.post("/api/skills", json={"name": "gh", "description": "desc"})
    assert res.status_code == 201
    assert res.json()["policy"] is None


def test_api_patch_skill_policy(client):
    created = client.post("/api/skills", json={"name": "gh", "description": "desc"}).json()
    res = client.patch(f"/api/skills/{created['id']}", json={"policy": "allow { true }"})
    assert res.status_code == 200
    assert res.json()["policy"] == "allow { true }"


def test_api_patch_clears_policy_to_null(client):
    created = client.post("/api/skills", json={
        "name": "gh", "description": "desc", "policy": "allow { true }"
    }).json()
    res = client.patch(f"/api/skills/{created['id']}", json={"policy": None})
    assert res.status_code == 200
    assert res.json()["policy"] is None


def test_api_patch_omitting_policy_leaves_it_unchanged(client):
    created = client.post("/api/skills", json={
        "name": "gh", "description": "desc", "policy": "allow { true }"
    }).json()
    res = client.patch(f"/api/skills/{created['id']}", json={"name": "gh-cli"})
    assert res.status_code == 200
    assert res.json()["policy"] == "allow { true }"


def test_api_skill_policy_returned_in_list(client):
    client.post("/api/skills", json={
        "name": "kubectl", "description": "k8s", "policy": "allow { true }"
    })
    skills = client.get("/api/skills").json()
    assert "policy" in skills[0]
    assert skills[0]["policy"] == "allow { true }"


# ── source field ───────────────────────────────────────────────────────────────

def test_skill_default_source_is_local():
    from datetime import datetime, timezone
    s = Skill(id="x", name="n", description="d", created_at=datetime.now(timezone.utc))
    assert s.source == "local"


def test_skill_source_remote():
    from datetime import datetime, timezone
    s = Skill(id="x", name="n", description="d", created_at=datetime.now(timezone.utc), source="remote")
    assert s.source == "remote"
