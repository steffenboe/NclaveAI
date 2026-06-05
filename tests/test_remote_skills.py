from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import RemoteSkillRepository, Skill


# ── YAML parsing ──────────────────────────────────────────────────────────────

def test_parse_full_yaml_file(tmp_path):
    (tmp_path / "kubectl.yaml").write_text(
        "name: kubectl\ndescription: Kubernetes CLI\nenabled: true\npolicy: |\n  allow { true }\n"
    )
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    skills = repo._parse_yaml_files()
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "kubectl"
    assert s.description == "Kubernetes CLI"
    assert s.enabled is True
    assert "allow { true }" in s.policy
    assert s.source == "remote"


def test_parse_minimal_yaml_file(tmp_path):
    (tmp_path / "simple.yaml").write_text("name: simple\ndescription: A simple skill\n")
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    skills = repo._parse_yaml_files()
    assert len(skills) == 1
    assert skills[0].enabled is True
    assert skills[0].policy is None


def test_parse_yaml_file_skips_subdirectory(tmp_path):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.yaml").write_text("name: nested\ndescription: Should be ignored\n")
    (tmp_path / "top.yaml").write_text("name: top\ndescription: Top level\n")
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    skills = repo._parse_yaml_files()
    assert len(skills) == 1
    assert skills[0].name == "top"


def test_parse_yaml_file_skips_malformed(tmp_path, caplog):
    (tmp_path / "bad.yaml").write_text(": invalid: yaml: {{{{")
    (tmp_path / "good.yaml").write_text("name: good\ndescription: Fine\n")
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    with caplog.at_level(logging.WARNING, logger="app.skills"):
        skills = repo._parse_yaml_files()
    assert len(skills) == 1
    assert skills[0].name == "good"


def test_parse_yaml_file_skips_missing_required_fields(tmp_path, caplog):
    (tmp_path / "noname.yaml").write_text("description: Missing name field\n")
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    with caplog.at_level(logging.WARNING, logger="app.skills"):
        skills = repo._parse_yaml_files()
    assert len(skills) == 0


def test_skill_ids_are_deterministic(tmp_path):
    (tmp_path / "kubectl.yaml").write_text("name: kubectl\ndescription: k8s CLI\n")
    repo_url = "https://example.com/repo"
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = repo_url
    repo._branch = "main"
    repo._skills = []
    skills1 = repo._parse_yaml_files()
    skills2 = repo._parse_yaml_files()
    assert skills1[0].id == skills2[0].id


# ── sync via subprocess ───────────────────────────────────────────────────────

def test_sync_clones_when_cache_empty(tmp_path):
    cache_dir = tmp_path / "cache"
    repo = RemoteSkillRepository("https://example.com/repo", branch="main", cache_dir=cache_dir)
    with patch("app.skills.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        # cache_dir has no .git → clone path
        repo.sync()
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "clone" in call_args


def test_sync_pulls_when_cache_exists(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ".git").mkdir()
    repo = RemoteSkillRepository("https://example.com/repo", branch="main", cache_dir=cache_dir)
    with patch("app.skills.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        repo.sync()
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "pull" in call_args


def test_sync_raises_on_git_failure(tmp_path):
    cache_dir = tmp_path / "cache"
    repo = RemoteSkillRepository("https://example.com/repo", branch="main", cache_dir=cache_dir)
    with patch("app.skills.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="fatal: repo not found")
        with pytest.raises(RuntimeError, match="git"):
            repo.sync()


def test_list_skills_returns_last_synced(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ".git").mkdir()
    (cache_dir / "tool.yaml").write_text("name: tool\ndescription: A tool\n")
    repo = RemoteSkillRepository("https://example.com/repo", branch="main", cache_dir=cache_dir)
    with patch("app.skills.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        repo.sync()
    skills = repo.list_skills()
    assert len(skills) == 1
    assert skills[0].name == "tool"
