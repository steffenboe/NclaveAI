"""API-level tests for the Teams endpoints."""
from __future__ import annotations

import pytest


@pytest.fixture
def client_with_teams(client):
    """TestClient with a fresh TeamRepository wired into app.state."""
    from app.teams import TeamRepository
    import app.main as main_module
    teams_path = main_module.app.state.skill_repo._path.parent / "teams.json"
    main_module.app.state.team_repo = TeamRepository(teams_path)
    return client


class TestTeamCRUD:
    def test_create_team(self, client_with_teams):
        r = client_with_teams.post("/api/teams", json={"name": "Engineering"})
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Engineering"
        assert data["user_ids"] == []
        assert data["skill_ids"] == []
        assert "team_id" in data

    def test_create_duplicate_name_returns_409(self, client_with_teams):
        client_with_teams.post("/api/teams", json={"name": "Dup"})
        r = client_with_teams.post("/api/teams", json={"name": "Dup"})
        assert r.status_code == 409

    def test_list_teams(self, client_with_teams):
        client_with_teams.post("/api/teams", json={"name": "A"})
        client_with_teams.post("/api/teams", json={"name": "B"})
        r = client_with_teams.get("/api/teams")
        assert r.status_code == 200
        names = [t["name"] for t in r.json()]
        assert "A" in names and "B" in names

    def test_get_team(self, client_with_teams):
        created = client_with_teams.post("/api/teams", json={"name": "Alpha"}).json()
        r = client_with_teams.get(f"/api/teams/{created['team_id']}")
        assert r.status_code == 200
        assert r.json()["name"] == "Alpha"

    def test_get_nonexistent_team_returns_404(self, client_with_teams):
        assert client_with_teams.get("/api/teams/does-not-exist").status_code == 404

    def test_update_team_name(self, client_with_teams):
        created = client_with_teams.post("/api/teams", json={"name": "Old"}).json()
        r = client_with_teams.put(f"/api/teams/{created['team_id']}", json={"name": "New"})
        assert r.status_code == 200
        assert r.json()["name"] == "New"

    def test_update_team_skills(self, client_with_teams):
        created = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        r = client_with_teams.put(f"/api/teams/{created['team_id']}", json={"skill_ids": ["skill-1", "skill-2"]})
        assert r.status_code == 200
        assert set(r.json()["skill_ids"]) == {"skill-1", "skill-2"}

    def test_update_team_llm_hides_api_key(self, client_with_teams):
        created = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        r = client_with_teams.put(f"/api/teams/{created['team_id']}", json={"llm_base_url": "https://my.llm", "llm_api_key": "secret"})
        assert r.status_code == 200
        body = r.json()
        assert body["llm_base_url"] == "https://my.llm"
        assert "llm_api_key" not in body
        assert body["has_llm_api_key"] is True

    def test_update_nonexistent_team_returns_404(self, client_with_teams):
        assert client_with_teams.put("/api/teams/ghost", json={"name": "X"}).status_code == 404

    def test_delete_team(self, client_with_teams):
        created = client_with_teams.post("/api/teams", json={"name": "ToDelete"}).json()
        assert client_with_teams.delete(f"/api/teams/{created['team_id']}").status_code == 204
        assert client_with_teams.get(f"/api/teams/{created['team_id']}").status_code == 404

    def test_delete_nonexistent_team_returns_404(self, client_with_teams):
        assert client_with_teams.delete("/api/teams/ghost").status_code == 404


class TestTeamMembers:
    def _get_user_id(self, client, username):
        return next(u["user_id"] for u in client.get("/api/users").json() if u["username"] == username)

    def test_add_member(self, client_with_teams):
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        alice_id = self._get_user_id(client_with_teams, "alice")
        r = client_with_teams.post(f"/api/teams/{team['team_id']}/members/{alice_id}")
        assert r.status_code == 200
        assert alice_id in r.json()["user_ids"]

    def test_add_member_nonexistent_user_returns_404(self, client_with_teams):
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        assert client_with_teams.post(f"/api/teams/{team['team_id']}/members/ghost").status_code == 404

    def test_add_member_nonexistent_team_returns_404(self, client_with_teams):
        alice_id = self._get_user_id(client_with_teams, "alice")
        assert client_with_teams.post(f"/api/teams/ghost/members/{alice_id}").status_code == 404

    def test_remove_member(self, client_with_teams):
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        alice_id = self._get_user_id(client_with_teams, "alice")
        client_with_teams.post(f"/api/teams/{team['team_id']}/members/{alice_id}")
        r = client_with_teams.delete(f"/api/teams/{team['team_id']}/members/{alice_id}")
        assert r.status_code == 200
        assert alice_id not in r.json()["user_ids"]

    def test_remove_nonmember_is_noop(self, client_with_teams):
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        alice_id = self._get_user_id(client_with_teams, "alice")
        r = client_with_teams.delete(f"/api/teams/{team['team_id']}/members/{alice_id}")
        assert r.status_code == 200
        assert alice_id not in r.json()["user_ids"]

    def test_user_in_multiple_teams(self, client_with_teams):
        t1 = client_with_teams.post("/api/teams", json={"name": "T1"}).json()
        t2 = client_with_teams.post("/api/teams", json={"name": "T2"}).json()
        alice_id = self._get_user_id(client_with_teams, "alice")
        client_with_teams.post(f"/api/teams/{t1['team_id']}/members/{alice_id}")
        client_with_teams.post(f"/api/teams/{t2['team_id']}/members/{alice_id}")
        teams = client_with_teams.get("/api/teams").json()
        assert len([t for t in teams if alice_id in t["user_ids"]]) == 2

    def test_delete_team_does_not_delete_user(self, client_with_teams):
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        alice_id = self._get_user_id(client_with_teams, "alice")
        client_with_teams.post(f"/api/teams/{team['team_id']}/members/{alice_id}")
        client_with_teams.delete(f"/api/teams/{team['team_id']}")
        assert any(u["user_id"] == alice_id for u in client_with_teams.get("/api/users").json())


class TestTeamRBAC:
    def test_non_admin_cannot_list_teams(self, client):
        from app.auth import get_current_user
        from app.main import app
        from app.models import User
        from datetime import datetime, timezone

        regular_user = User(user_id="regular-id", username="alice", hashed_password="", role="user", created_at=datetime.now(timezone.utc))
        app.dependency_overrides[get_current_user] = lambda: regular_user
        try:
            assert client.get("/api/teams").status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
