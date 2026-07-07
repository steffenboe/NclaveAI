"""Unit tests for TeamRepository and the team skill/LLM resolution helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.teams import TeamRepository, get_team_assigned_skill_ids, resolve_team_llm, resolve_team_skills


def make_repo(tmp_path: Path) -> TeamRepository:
    return TeamRepository(tmp_path / "teams.json")


class TestTeamRepositoryCRUD:
    def test_create_and_get(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="Engineering")
        assert team.team_id
        assert team.name == "Engineering"
        assert team.user_ids == []
        assert team.skill_ids == []
        fetched = repo.get(team.team_id)
        assert fetched is not None
        assert fetched.team_id == team.team_id

    def test_create_duplicate_name_raises(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.create(name="Alpha")
        with pytest.raises(ValueError, match="already exists"):
            repo.create(name="Alpha")

    def test_list(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.create(name="A")
        repo.create(name="B")
        assert len(repo.list()) == 2

    def test_update(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="Old")
        updated = repo.update(team.team_id, name="New")
        assert updated.name == "New"
        assert repo.get(team.team_id).name == "New"

    def test_update_nonexistent_raises(self, tmp_path):
        repo = make_repo(tmp_path)
        with pytest.raises(KeyError):
            repo.update("nonexistent-id", name="X")

    def test_delete(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="ToDelete")
        repo.delete(team.team_id)
        assert repo.get(team.team_id) is None

    def test_delete_nonexistent_raises(self, tmp_path):
        repo = make_repo(tmp_path)
        with pytest.raises(KeyError):
            repo.delete("nonexistent-id")

    def test_persistence_across_instances(self, tmp_path):
        repo1 = make_repo(tmp_path)
        team = repo1.create(name="Persisted")
        repo2 = make_repo(tmp_path)
        assert repo2.get(team.team_id) is not None
        assert repo2.get(team.team_id).name == "Persisted"


class TestTeamMembership:
    def test_add_member(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="T1")
        updated = repo.add_member(team.team_id, "user-1")
        assert "user-1" in updated.user_ids

    def test_add_member_idempotent(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="T1")
        repo.add_member(team.team_id, "user-1")
        repo.add_member(team.team_id, "user-1")
        assert repo.get(team.team_id).user_ids.count("user-1") == 1

    def test_remove_member(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="T1")
        repo.add_member(team.team_id, "user-1")
        updated = repo.remove_member(team.team_id, "user-1")
        assert "user-1" not in updated.user_ids

    def test_remove_nonmember_is_noop(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="T1")
        updated = repo.remove_member(team.team_id, "ghost")
        assert updated.user_ids == []

    def test_list_by_user_returns_correct_teams(self, tmp_path):
        repo = make_repo(tmp_path)
        t1 = repo.create(name="T1")
        t2 = repo.create(name="T2")
        repo.add_member(t1.team_id, "alice")
        repo.add_member(t2.team_id, "bob")
        result = repo.list_by_user("alice")
        assert len(result) == 1
        assert result[0].team_id == t1.team_id

    def test_list_by_user_multiple_teams(self, tmp_path):
        repo = make_repo(tmp_path)
        t1 = repo.create(name="T1")
        t2 = repo.create(name="T2")
        repo.add_member(t1.team_id, "alice")
        repo.add_member(t2.team_id, "alice")
        result = repo.list_by_user("alice")
        assert {r.team_id for r in result} == {t1.team_id, t2.team_id}

    def test_list_by_user_no_teams(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.create(name="T1")
        assert repo.list_by_user("nobody") == []


class TestResolveTeamSkills:
    def _make_skill(self, skill_id, name="skill"):
        s = MagicMock()
        s.id = skill_id
        s.name = name
        return s

    def test_no_team_membership_returns_none(self, tmp_path):
        repo = make_repo(tmp_path)
        skill_repo = MagicMock()
        assert resolve_team_skills("orphan", repo, skill_repo, {}) is None

    def test_single_team_filters_skills(self, tmp_path):
        repo = make_repo(tmp_path)
        skill_a = self._make_skill("skill-a")
        skill_b = self._make_skill("skill-b")
        team = repo.create(name="T1", skill_ids=["skill-a"])
        repo.add_member(team.team_id, "alice")
        skill_repo = MagicMock()
        skill_repo.list.return_value = [skill_a, skill_b]
        local, remote = resolve_team_skills("alice", repo, skill_repo, {})
        assert len(local) == 1 and local[0].id == "skill-a"
        assert remote == []

    def test_multi_team_union_of_skills(self, tmp_path):
        repo = make_repo(tmp_path)
        skill_a = self._make_skill("skill-a")
        skill_b = self._make_skill("skill-b")
        skill_c = self._make_skill("skill-c")
        t1 = repo.create(name="T1", skill_ids=["skill-a"])
        t2 = repo.create(name="T2", skill_ids=["skill-b"])
        repo.add_member(t1.team_id, "alice")
        repo.add_member(t2.team_id, "alice")
        skill_repo = MagicMock()
        skill_repo.list.return_value = [skill_a, skill_b, skill_c]
        local, _ = resolve_team_skills("alice", repo, skill_repo, {})
        assert {s.id for s in local} == {"skill-a", "skill-b"}

    def test_team_with_cached_remote_repo_includes_skills(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="T", skill_repo_url="https://example.com/skills")
        repo.add_member(team.team_id, "alice")
        skill_repo = MagicMock()
        skill_repo.list.return_value = []
        remote_skill = self._make_skill("remote-1")
        mock_repo = MagicMock()
        mock_repo.list_skills.return_value = [remote_skill]
        team_remote_repos = {"https://example.com/skills": mock_repo}
        _, remote = resolve_team_skills("alice", repo, skill_repo, team_remote_repos)
        assert remote == [remote_skill]

    def test_uncached_remote_repo_url_is_skipped(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="T", skill_repo_url="https://uncached.example.com")
        repo.add_member(team.team_id, "alice")
        skill_repo = MagicMock()
        skill_repo.list.return_value = []
        _, remote = resolve_team_skills("alice", repo, skill_repo, {})
        assert remote == []

    def test_deduplicates_remote_repo_url(self, tmp_path):
        repo = make_repo(tmp_path)
        t1 = repo.create(name="T1", skill_repo_url="https://same.example.com/repo")
        t2 = repo.create(name="T2", skill_repo_url="https://same.example.com/repo")
        repo.add_member(t1.team_id, "alice")
        repo.add_member(t2.team_id, "alice")
        skill_repo = MagicMock()
        skill_repo.list.return_value = []
        mock_repo = MagicMock()
        mock_repo.list_skills.return_value = []
        team_remote_repos = {"https://same.example.com/repo": mock_repo}
        resolve_team_skills("alice", repo, skill_repo, team_remote_repos)
        mock_repo.list_skills.assert_called_once()


class TestResolveTeamLlm:
    def test_no_teams_returns_global(self, tmp_path):
        repo = make_repo(tmp_path)
        url, key = resolve_team_llm("alice", repo, "https://global.api", "global-key")
        assert url == "https://global.api" and key == "global-key"

    def test_team_without_llm_falls_back(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="NoLLM")
        repo.add_member(team.team_id, "alice")
        url, _ = resolve_team_llm("alice", repo, "https://global.api", "global-key")
        assert url == "https://global.api"

    def test_team_with_llm_overrides_global(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="T", llm_base_url="https://team.api", llm_api_key="team-key")
        repo.add_member(team.team_id, "alice")
        url, key = resolve_team_llm("alice", repo, "https://global.api", "global-key")
        assert url == "https://team.api" and key == "team-key"

    def test_first_team_with_llm_wins(self, tmp_path):
        repo = make_repo(tmp_path)
        t1 = repo.create(name="T1", llm_base_url="https://team1.api", llm_api_key="k1")
        t2 = repo.create(name="T2", llm_base_url="https://team2.api", llm_api_key="k2")
        repo.add_member(t1.team_id, "alice")
        repo.add_member(t2.team_id, "alice")
        url, _ = resolve_team_llm("alice", repo, "https://global.api", "global-key")
        assert url == "https://team1.api"


class TestGetTeamAssignedSkillIds:
    def test_empty_when_no_teams(self, tmp_path):
        repo = make_repo(tmp_path)
        assert get_team_assigned_skill_ids(repo) == set()

    def test_empty_when_teams_have_no_skills(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.create(name="T1")
        assert get_team_assigned_skill_ids(repo) == set()

    def test_returns_all_assigned_ids(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.create(name="T1", skill_ids=["skill-a", "skill-b"])
        repo.create(name="T2", skill_ids=["skill-b", "skill-c"])
        assert get_team_assigned_skill_ids(repo) == {"skill-a", "skill-b", "skill-c"}


class TestGlobalSkillsExcludeTeamPrivate:
    """resolve_team_skills returns None for non-members; _build_workflow must
    then exclude team-assigned skills from the global pool."""

    def _make_skill(self, skill_id, name="skill"):
        s = MagicMock()
        s.id = skill_id
        s.name = name
        return s

    def test_team_assigned_skill_excluded_from_global_pool(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.create(name="T1", skill_ids=["skill-private"])

        skill_private = self._make_skill("skill-private")
        skill_global = self._make_skill("skill-global")
        skill_repo = MagicMock()
        skill_repo.list.return_value = [skill_private, skill_global]

        # Non-member → resolve_team_skills returns None
        result = resolve_team_skills("bob", repo, skill_repo, {})
        assert result is None

        # Caller (like _build_workflow) must then filter out team-assigned skills
        team_assigned = get_team_assigned_skill_ids(repo)
        global_pool = [s for s in skill_repo.list() if s.id not in team_assigned]
        assert len(global_pool) == 1
        assert global_pool[0].id == "skill-global"

    def test_team_member_gets_private_skill(self, tmp_path):
        repo = make_repo(tmp_path)
        team = repo.create(name="T1", skill_ids=["skill-private"])
        repo.add_member(team.team_id, "alice")

        skill_private = self._make_skill("skill-private")
        skill_global = self._make_skill("skill-global")
        skill_repo = MagicMock()
        skill_repo.list.return_value = [skill_private, skill_global]

        local, _ = resolve_team_skills("alice", repo, skill_repo, {})
        assert len(local) == 1
        assert local[0].id == "skill-private"
