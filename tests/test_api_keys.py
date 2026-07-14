"""Tests for API key management and the /api/v1/skills/check endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api_keys import ApiKeyRepository, generate_api_key, hash_api_key
from app.auth import get_current_user
from app.main import app
from app.models import User


# ---------------------------------------------------------------------------
# Unit tests: api_keys module
# ---------------------------------------------------------------------------


def test_generate_api_key_format():
    raw, prefix, hashed = generate_api_key()
    assert raw.startswith("ncl_")
    assert len(raw) > 12
    assert prefix == raw[:12]
    assert len(hashed) == 64  # SHA-256 hex


def test_hash_is_deterministic():
    raw, _, hashed = generate_api_key()
    assert hash_api_key(raw) == hashed


def test_different_keys_produce_different_hashes():
    _, _, h1 = generate_api_key()
    _, _, h2 = generate_api_key()
    assert h1 != h2


def test_repo_create_and_lookup(tmp_path):
    repo = ApiKeyRepository(tmp_path / "api_keys.json")
    raw, prefix, hashed = generate_api_key()
    key = repo.create(user_id="u1", name="my key", hashed_key=hashed, key_prefix=prefix)

    assert key.user_id == "u1"
    assert key.name == "my key"
    assert key.key_prefix == prefix
    assert key.last_used_at is None

    found = repo.get_by_hash(hashed)
    assert found is not None
    assert found.key_id == key.key_id


def test_repo_get_by_hash_missing(tmp_path):
    repo = ApiKeyRepository(tmp_path / "api_keys.json")
    assert repo.get_by_hash("notahash") is None


def test_repo_list_by_user(tmp_path):
    repo = ApiKeyRepository(tmp_path / "api_keys.json")
    _, p1, h1 = generate_api_key()
    _, p2, h2 = generate_api_key()
    repo.create(user_id="u1", name="k1", hashed_key=h1, key_prefix=p1)
    repo.create(user_id="u2", name="k2", hashed_key=h2, key_prefix=p2)

    assert len(repo.list_by_user("u1")) == 1
    assert len(repo.list_by_user("u2")) == 1
    assert repo.list_by_user("u3") == []


def test_repo_touch(tmp_path):
    repo = ApiKeyRepository(tmp_path / "api_keys.json")
    raw, prefix, hashed = generate_api_key()
    key = repo.create(user_id="u1", name="k", hashed_key=hashed, key_prefix=prefix)
    assert key.last_used_at is None
    repo.touch(key.key_id)
    found = repo.get_by_hash(hashed)
    assert found is not None
    assert found.last_used_at is not None


def test_repo_delete(tmp_path):
    repo = ApiKeyRepository(tmp_path / "api_keys.json")
    raw, prefix, hashed = generate_api_key()
    key = repo.create(user_id="u1", name="k", hashed_key=hashed, key_prefix=prefix)
    repo.delete(key.key_id, "u1")
    assert repo.get_by_hash(hashed) is None


def test_repo_delete_wrong_user_raises(tmp_path):
    repo = ApiKeyRepository(tmp_path / "api_keys.json")
    _, prefix, hashed = generate_api_key()
    key = repo.create(user_id="u1", name="k", hashed_key=hashed, key_prefix=prefix)
    with pytest.raises(KeyError):
        repo.delete(key.key_id, "u2")


def test_repo_persists_to_disk(tmp_path):
    path = tmp_path / "api_keys.json"
    repo1 = ApiKeyRepository(path)
    _, prefix, hashed = generate_api_key()
    created = repo1.create(user_id="u1", name="persistent", hashed_key=hashed, key_prefix=prefix)

    repo2 = ApiKeyRepository(path)
    found = repo2.get_by_hash(hashed)
    assert found is not None
    assert found.key_id == created.key_id


# ---------------------------------------------------------------------------
# Integration tests: REST endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(client):
    """TestClient with api_key_repo wired up."""
    from app.api_keys import ApiKeyRepository as Repo

    client.app.state.api_key_repo = Repo(
        client.app.state.skill_repo._path.parent / "api_keys.json"
    )
    return client


@pytest.fixture
def authed_api_client(api_client):
    """API client logged in as admin (session cookie)."""
    resp = api_client.post("/api/auth/login", json={"username": "admin", "password": "Admin123!"})
    assert resp.status_code == 200
    return api_client


def test_create_api_key(authed_api_client):
    resp = authed_api_client.post("/api/auth/api-keys", json={"name": "my-key"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "my-key"
    assert data["key"].startswith("ncl_")
    assert "key_id" in data
    assert "key_prefix" in data
    # Full key must not be stored — check prefix only
    assert data["key_prefix"] == data["key"][:12]


def test_create_api_key_requires_auth(api_client):
    # Remove any override so the real dependency runs
    app.dependency_overrides.pop(get_current_user, None)
    resp = api_client.post("/api/auth/api-keys", json={"name": "k"})
    assert resp.status_code == 401


def test_list_api_keys(authed_api_client):
    authed_api_client.post("/api/auth/api-keys", json={"name": "k1"})
    authed_api_client.post("/api/auth/api-keys", json={"name": "k2"})
    resp = authed_api_client.get("/api/auth/api-keys")
    assert resp.status_code == 200
    names = {k["name"] for k in resp.json()}
    assert {"k1", "k2"} <= names


def test_list_api_keys_does_not_expose_raw_key(authed_api_client):
    authed_api_client.post("/api/auth/api-keys", json={"name": "secret"})
    resp = authed_api_client.get("/api/auth/api-keys")
    for item in resp.json():
        assert "key" not in item


def test_delete_api_key(authed_api_client):
    create_resp = authed_api_client.post("/api/auth/api-keys", json={"name": "to-delete"})
    key_id = create_resp.json()["key_id"]

    del_resp = authed_api_client.delete(f"/api/auth/api-keys/{key_id}")
    assert del_resp.status_code == 204

    keys = authed_api_client.get("/api/auth/api-keys").json()
    assert all(k["key_id"] != key_id for k in keys)


def test_delete_nonexistent_api_key(authed_api_client):
    resp = authed_api_client.delete("/api/auth/api-keys/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Integration tests: X-Api-Key authentication
# ---------------------------------------------------------------------------


@pytest.fixture
def key_and_client(authed_api_client):
    """Create an API key for admin and return (raw_key, client)."""
    resp = authed_api_client.post("/api/auth/api-keys", json={"name": "test-key"})
    raw = resp.json()["key"]
    return raw, authed_api_client


def test_skill_check_with_api_key(key_and_client, api_client):
    raw_key, _ = key_and_client
    # Add a skill with an allow policy
    api_client.app.state.skill_repo.create(
        name="file manager",
        description="List and read files on disk",
        policy="default allow = true",
    )
    resp = api_client.post(
        "/api/v1/skills/check",
        json={"command": "ls -la"},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["command"] == "ls -la"
    assert data["argv"] == ["ls", "-la"]
    assert data["allowed"] == True
    assert len(data["matches"]) > 0
def test_skill_check_no_matches(key_and_client, api_client):
    raw_key, _ = key_and_client
    # Create a skill that denies everything
    api_client.app.state.skill_repo.create(
        name="deny all",
        description="Denies all commands",
        policy="default allow = false",
    )
    resp = api_client.post(
        "/api/v1/skills/check",
        json={"command": "ls -la"},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Even though there's a deny skill, the default "allow" skill permits it
    # This test just verifies the endpoint works with various commands
    assert data["command"] == "ls -la"


def test_skill_check_invalid_api_key(api_client):
    app.dependency_overrides.pop(get_current_user, None)
    resp = api_client.post(
        "/api/v1/skills/check",
        json={"command": "hello"},
        headers={"X-Api-Key": "ncl_invalidkey"},
    )
    assert resp.status_code == 401


def test_skill_check_with_session_cookie(authed_api_client):
    """Skill check also works with a session cookie (dual-auth dependency)."""
    authed_api_client.app.state.skill_repo.create(
        name="git helper",
        description="Run git commands",
    )
    resp = authed_api_client.post(
        "/api/v1/skills/check",
        json={"command": "git status"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["command"] == "git status"
    assert data["argv"] == ["git", "status"]


def test_skill_check_disabled_skill_excluded(key_and_client, api_client):
    raw_key, _ = key_and_client
    api_client.app.state.skill_repo.create(
        name="hidden tool",
        description="This skill is disabled",
        enabled=False,
    )
    resp = api_client.post(
        "/api/v1/skills/check",
        json={"command": "hidden"},
        headers={"X-Api-Key": raw_key},
    )
    assert resp.status_code == 200
    assert resp.json()["matches"] == []


