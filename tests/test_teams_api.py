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


class TestTeamSkillVisibility:
    """Tests for team-based skill visibility: users not in a group cannot see skills assigned to groups."""

    def _get_user_id(self, client, username):
        return next(u["user_id"] for u in client.get("/api/users").json() if u["username"] == username)

    def test_user_in_team_sees_team_skills(self, client_with_teams):
        """A user in a team should see skills assigned to that team."""
        from app.auth import get_current_user
        from app.main import app
        from app.models import User
        from datetime import datetime, timezone
        from app.skills import Skill

        # Create skills
        alice_id = self._get_user_id(client_with_teams, "alice")
        skill_repo = app.state.skill_repo
        skill1 = skill_repo.create(name="skill-a", description="Skill A")
        skill2 = skill_repo.create(name="skill-b", description="Skill B")

        # Create team and add skill + user
        team = client_with_teams.post("/api/teams", json={"name": "TeamA"}).json()
        client_with_teams.put(f"/api/teams/{team['team_id']}", json={"skill_ids": [skill1.id]})
        client_with_teams.post(f"/api/teams/{team['team_id']}/members/{alice_id}")

        # Override current user to alice
        regular_user = User(user_id=alice_id, username="alice", hashed_password="", role="user", created_at=datetime.now(timezone.utc))
        app.dependency_overrides[get_current_user] = lambda: regular_user
        try:
            skills = client_with_teams.get("/api/skills").json()
            skill_ids = {s["id"] for s in skills}
            assert skill1.id in skill_ids, "User in team should see team skill"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_user_not_in_team_cannot_see_team_skills(self, client_with_teams):
        """A user not in a team should not see skills assigned to that team."""
        from app.auth import get_current_user
        from app.main import app
        from app.models import User
        from datetime import datetime, timezone

        # Create skills
        alice_id = self._get_user_id(client_with_teams, "alice")
        bob_id = self._get_user_id(client_with_teams, "bob")
        skill_repo = app.state.skill_repo
        skill1 = skill_repo.create(name="secret-skill", description="Secret Skill")
        skill2 = skill_repo.create(name="public-skill", description="Public Skill")

        # Create team with alice, assign secret skill to team
        team = client_with_teams.post("/api/teams", json={"name": "SecretTeam"}).json()
        client_with_teams.put(f"/api/teams/{team['team_id']}", json={"skill_ids": [skill1.id]})
        client_with_teams.post(f"/api/teams/{team['team_id']}/members/{alice_id}")

        # Override current user to bob (not in team)
        regular_user = User(user_id=bob_id, username="bob", hashed_password="", role="user", created_at=datetime.now(timezone.utc))
        app.dependency_overrides[get_current_user] = lambda: regular_user
        try:
            skills = client_with_teams.get("/api/skills").json()
            skill_ids = {s["id"] for s in skills}
            assert skill1.id not in skill_ids, "User not in team should not see team skill"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_user_in_multiple_teams_sees_all_team_skills(self, client_with_teams):
        """A user in multiple teams should see skills from all their teams."""
        from app.auth import get_current_user
        from app.main import app
        from app.models import User
        from datetime import datetime, timezone

        alice_id = self._get_user_id(client_with_teams, "alice")
        skill_repo = app.state.skill_repo
        skill1 = skill_repo.create(name="skill-team1", description="Skill Team 1")
        skill2 = skill_repo.create(name="skill-team2", description="Skill Team 2")

        # Create two teams and assign different skills
        team1 = client_with_teams.post("/api/teams", json={"name": "Team1"}).json()
        team2 = client_with_teams.post("/api/teams", json={"name": "Team2"}).json()
        client_with_teams.put(f"/api/teams/{team1['team_id']}", json={"skill_ids": [skill1.id]})
        client_with_teams.put(f"/api/teams/{team2['team_id']}", json={"skill_ids": [skill2.id]})

        # Add alice to both teams
        client_with_teams.post(f"/api/teams/{team1['team_id']}/members/{alice_id}")
        client_with_teams.post(f"/api/teams/{team2['team_id']}/members/{alice_id}")

        # Override current user to alice
        regular_user = User(user_id=alice_id, username="alice", hashed_password="", role="user", created_at=datetime.now(timezone.utc))
        app.dependency_overrides[get_current_user] = lambda: regular_user
        try:
            skills = client_with_teams.get("/api/skills").json()
            skill_ids = {s["id"] for s in skills}
            assert skill1.id in skill_ids, "User in team1 should see skill1"
            assert skill2.id in skill_ids, "User in team2 should see skill2"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_user_not_in_any_team_cannot_use_skills(self, client_with_teams):
        """A user not in any team should not see any team-assigned skills."""
        from app.auth import get_current_user
        from app.main import app
        from app.models import User
        from datetime import datetime, timezone

        bob_id = self._get_user_id(client_with_teams, "bob")
        alice_id = self._get_user_id(client_with_teams, "alice")
        skill_repo = app.state.skill_repo
        skill1 = skill_repo.create(name="team-exclusive-skill", description="Team Exclusive")

        # Create team with alice, assign skill
        team = client_with_teams.post("/api/teams", json={"name": "ExclusiveTeam"}).json()
        client_with_teams.put(f"/api/teams/{team['team_id']}", json={"skill_ids": [skill1.id]})
        client_with_teams.post(f"/api/teams/{team['team_id']}/members/{alice_id}")

        # Override current user to bob (not in any team)
        regular_user = User(user_id=bob_id, username="bob", hashed_password="", role="user", created_at=datetime.now(timezone.utc))
        app.dependency_overrides[get_current_user] = lambda: regular_user
        try:
            skills = client_with_teams.get("/api/skills").json()
            skill_ids = {s["id"] for s in skills}
            assert skill1.id not in skill_ids, "User not in any team should not see team-exclusive skill"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_admin_sees_team_assigned_skills(self, client_with_teams):
        """Admins should see all skills, including team-assigned ones, in settings."""
        from app.auth import get_current_user
        from app.main import app
        from app.models import User
        from datetime import datetime, timezone

        skill_repo = app.state.skill_repo
        team_skill = skill_repo.create(name="team-visible-for-admin", description="Team Skill")
        global_skill = skill_repo.create(name="global-visible-for-admin", description="Global Skill")

        team = client_with_teams.post("/api/teams", json={"name": "AdminCheckTeam"}).json()
        client_with_teams.put(f"/api/teams/{team['team_id']}", json={"skill_ids": [team_skill.id]})

        admin_user = User(
            user_id="admin-id",
            username="admin",
            hashed_password="",
            role="admin",
            created_at=datetime.now(timezone.utc),
        )
        app.dependency_overrides[get_current_user] = lambda: admin_user
        try:
            skills = client_with_teams.get("/api/skills").json()
            skill_ids = {s["id"] for s in skills}
            assert team_skill.id in skill_ids
            assert global_skill.id in skill_ids
        finally:
            app.dependency_overrides.pop(get_current_user, None)


class TestSkillTeamAssignment:
    """Tests for the new skill.team_id model: skills can only be assigned to one team."""

    def _get_user_id(self, client, username):
        return next(u["user_id"] for u in client.get("/api/users").json() if u["username"] == username)

    def test_global_skills_visible_to_everyone(self, client_with_teams):
        """Global skills (team_id = None) should be visible to everyone."""
        from app.auth import get_current_user
        from app.main import app
        from app.models import User
        from datetime import datetime, timezone

        alice_id = self._get_user_id(client_with_teams, "alice")
        bob_id = self._get_user_id(client_with_teams, "bob")
        skill_repo = app.state.skill_repo
        
        # Create a global skill (team_id = None)
        global_skill = skill_repo.create(name="global-skill", description="Global Skill", team_id=None)
        
        # Create team with alice but assign no skills
        team = client_with_teams.post("/api/teams", json={"name": "TeamA"}).json()
        client_with_teams.post(f"/api/teams/{team['team_id']}/members/{alice_id}")

        # Test alice (in team) sees global skill
        regular_user = User(user_id=alice_id, username="alice", hashed_password="", role="user", created_at=datetime.now(timezone.utc))
        app.dependency_overrides[get_current_user] = lambda: regular_user
        try:
            skills = client_with_teams.get("/api/skills").json()
            skill_ids = {s["id"] for s in skills}
            assert global_skill.id in skill_ids, "Team member should see global skill"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        # Test bob (not in team) sees global skill
        regular_user = User(user_id=bob_id, username="bob", hashed_password="", role="user", created_at=datetime.now(timezone.utc))
        app.dependency_overrides[get_current_user] = lambda: regular_user
        try:
            skills = client_with_teams.get("/api/skills").json()
            skill_ids = {s["id"] for s in skills}
            assert global_skill.id in skill_ids, "Non-team member should see global skill"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_skill_team_id_synced_with_team_skill_ids(self, client_with_teams):
        """When updating team.skill_ids, skill.team_id should be updated accordingly."""
        from app.main import app

        skill_repo = app.state.skill_repo
        
        # Create two skills
        skill1 = skill_repo.create(name="skill1", description="Skill 1")
        skill2 = skill_repo.create(name="skill2", description="Skill 2")
        
        # Create team and assign skill1
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        client_with_teams.put(f"/api/teams/{team['team_id']}", json={"skill_ids": [skill1.id]})
        
        # Check that skill1.team_id is set to team_id
        updated_skill1 = skill_repo.get(skill1.id)
        assert updated_skill1.team_id == team['team_id'], "Skill should be assigned to team"
        
        # Now update team to assign skill2 instead
        client_with_teams.put(f"/api/teams/{team['team_id']}", json={"skill_ids": [skill2.id]})
        
        # skill1.team_id should be cleared
        updated_skill1 = skill_repo.get(skill1.id)
        assert updated_skill1.team_id is None, "Skill should be unassigned from team"
        
        # skill2.team_id should be set
        updated_skill2 = skill_repo.get(skill2.id)
        assert updated_skill2.team_id == team['team_id'], "Skill should be assigned to team"

    def test_create_skill_with_team_id(self, client_with_teams):
        """Admin can create a skill directly assigned to a team."""
        from app.main import app

        # Create team
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        
        # Create skill with team_id
        r = client_with_teams.post("/api/skills", json={
            "name": "team-skill",
            "description": "Team Skill",
            "team_id": team['team_id']
        })
        assert r.status_code == 201
        skill_data = r.json()
        assert skill_data["team_id"] == team['team_id'], "Skill should have team_id set"

    def test_update_skill_team_id(self, client_with_teams):
        """Admin can update a skill's team_id."""
        from app.main import app

        skill_repo = app.state.skill_repo
        
        # Create skill without team
        skill = skill_repo.create(name="skill", description="Skill")
        assert skill.team_id is None
        
        # Create team
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        
        # Update skill to assign to team
        r = client_with_teams.patch(f"/api/skills/{skill.id}", json={"team_id": team['team_id']})
        assert r.status_code == 200
        skill_data = r.json()
        assert skill_data["team_id"] == team['team_id'], "Skill should be assigned to team"

    def test_skill_unassigned_from_team_becomes_global(self, client_with_teams):
        """When a skill's team_id is set to None, it becomes global."""
        from app.main import app

        skill_repo = app.state.skill_repo
        
        # Create team and skill
        team = client_with_teams.post("/api/teams", json={"name": "T"}).json()
        skill = skill_repo.create(name="skill", description="Skill", team_id=team['team_id'])
        
        # Update skill to remove team assignment
        r = client_with_teams.patch(f"/api/skills/{skill.id}", json={"team_id": None})
        assert r.status_code == 200
        skill_data = r.json()
        assert skill_data["team_id"] is None, "Skill should be global"
