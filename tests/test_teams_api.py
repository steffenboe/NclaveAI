"""API-level tests for the Teams endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.audit import FileAuditRepository
from app.main import app
from app.runs import RunRepository
from app.scheduled_tasks import ScheduledTaskRepository
from app.secrets_store import SecretsStore
from app.settings_store import AppSettingsRepository
from app.skills import SkillRepository
from app.teams import TeamRepository
from app.users import UserRepository


@pytest.fixture
def client(tmp_path):
    """TestClient with all file-backed repos set directly — no lifespan / no MongoDB."""
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    app.state.user_repo = UserRepository(tmp_path / "users.json")
    app.state.run_repo = RunRepository(tmp_path / "runs.json")
    app.state.scheduled_task_repo = ScheduledTaskRepository(tmp_path / "tasks.json")
    app.state.secrets_store = SecretsStore(tmp_path / "secrets.json")
    app.state.app_settings_repo = AppSettingsRepository(tmp_path / "settings.json")
    app.state.audit_repo = FileAuditRepository(tmp_path / "audit.jsonl")
    app.state.remote_skill_repo = None
    app.state.team_remote_repos = {}
    app.state.team_repo = TeamRepository(tmp_path / "teams.json")

    # seed a regular user for membership tests
    app.state.user_repo.create(username="alice", hashed_password="x", role="user")

    return TestClient(app)


class TestTeamCRUD:
    def test_create_team(self, client):
        r = client.post("/api/teams", json={"name": "Engineering"})
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Engineering"
        assert data["user_ids"] == []
        assert data["skill_ids"] == []
        assert "team_id" in data
        assert "has_llm_api_key" in data
        assert "llm_api_key" not in data

    def test_create_duplicate_name_returns_409(self, client):
        client.post("/api/teams", json={"name": "Dup"})
        r = client.post("/api/teams", json={"name": "Dup"})
        assert r.status_code == 409

    def test_list_teams(self, client):
        client.post("/api/teams", json={"name": "A"})
        client.post("/api/teams", json={"name": "B"})
        r = client.get("/api/teams")
        assert r.status_code == 200
        names = [t["name"] for t in r.json()]
        assert "A" in names and "B" in names

    def test_get_team(self, client):
        created = client.post("/api/teams", json={"name": "Alpha"}).json()
        r = client.get(f"/api/teams/{created['team_id']}")
        assert r.status_code == 200
        assert r.json()["name"] == "Alpha"

    def test_get_nonexistent_team_returns_404(self, client):
        assert client.get("/api/teams/does-not-exist").status_code == 404

    def test_update_team_name(self, client):
        created = client.post("/api/teams", json={"name": "Old"}).json()
        r = client.put(f"/api/teams/{created['team_id']}", json={"name": "New"})
        assert r.status_code == 200
        assert r.json()["name"] == "New"

    def test_update_team_skills(self, client):
        created = client.post("/api/teams", json={"name": "T"}).json()
        r = client.put(
            f"/api/teams/{created['team_id']}",
            json={"skill_ids": ["skill-1", "skill-2"]},
        )
        assert r.status_code == 200
        assert set(r.json()["skill_ids"]) == {"skill-1", "skill-2"}

    def test_update_team_llm_hides_api_key(self, client):
        created = client.post("/api/teams", json={"name": "T"}).json()
        r = client.put(
            f"/api/teams/{created['team_id']}",
            json={"llm_base_url": "https://my.llm", "llm_api_key": "secret"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["llm_base_url"] == "https://my.llm"
        assert "llm_api_key" not in body
        assert body["has_llm_api_key"] is True

    def test_update_nonexistent_team_returns_404(self, client):
        assert client.put("/api/teams/ghost", json={"name": "X"}).status_code == 404

    def test_delete_team(self, client):
        created = client.post("/api/teams", json={"name": "ToDelete"}).json()
        assert client.delete(f"/api/teams/{created['team_id']}").status_code == 204
        assert client.get(f"/api/teams/{created['team_id']}").status_code == 404

    def test_delete_nonexistent_team_returns_404(self, client):
        assert client.delete("/api/teams/ghost").status_code == 404


class TestTeamMembers:
    def _alice_id(self, client):
        return next(u["user_id"] for u in client.get("/api/users").json() if u["username"] == "alice")

    def test_add_member(self, client):
        team = client.post("/api/teams", json={"name": "T"}).json()
        alice_id = self._alice_id(client)
        r = client.post(f"/api/teams/{team['team_id']}/members/{alice_id}")
        assert r.status_code == 200
        assert alice_id in r.json()["user_ids"]

    def test_add_member_nonexistent_user_returns_404(self, client):
        team = client.post("/api/teams", json={"name": "T"}).json()
        assert client.post(f"/api/teams/{team['team_id']}/members/ghost").status_code == 404

    def test_add_member_nonexistent_team_returns_404(self, client):
        alice_id = self._alice_id(client)
        assert client.post(f"/api/teams/ghost/members/{alice_id}").status_code == 404

    def test_remove_member(self, client):
        team = client.post("/api/teams", json={"name": "T"}).json()
        alice_id = self._alice_id(client)
        client.post(f"/api/teams/{team['team_id']}/members/{alice_id}")
        r = client.delete(f"/api/teams/{team['team_id']}/members/{alice_id}")
        assert r.status_code == 200
        assert alice_id not in r.json()["user_ids"]

    def test_remove_member_nonexistent_user_returns_404(self, client):
        team = client.post("/api/teams", json={"name": "T"}).json()
        assert client.delete(f"/api/teams/{team['team_id']}/members/ghost").status_code == 404

    def test_remove_member_nonexistent_team_returns_404(self, client):
        alice_id = self._alice_id(client)
        assert client.delete(f"/api/teams/ghost/members/{alice_id}").status_code == 404
