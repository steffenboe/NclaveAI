from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.settings_store import AppSettings, AppSettingsRepository


def test_load_returns_defaults_when_file_missing(tmp_path):
    repo = AppSettingsRepository(tmp_path / "settings.json")
    s = repo.load()
    assert s.skills_repo_url is None
    assert s.skills_repo_branch == "main"


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    repo = AppSettingsRepository(path)
    repo.save(AppSettings(skills_repo_url="https://example.com/repo", skills_repo_branch="develop"))
    loaded = repo.load()
    assert loaded.skills_repo_url == "https://example.com/repo"
    assert loaded.skills_repo_branch == "develop"


def test_save_writes_valid_json(tmp_path):
    path = tmp_path / "settings.json"
    repo = AppSettingsRepository(path)
    repo.save(AppSettings(skills_repo_url="https://example.com/repo"))
    data = json.loads(path.read_text())
    assert data["skills_repo_url"] == "https://example.com/repo"


def test_load_logs_warning_on_malformed_json(tmp_path, caplog):
    path = tmp_path / "settings.json"
    path.write_text("not json {{{")
    repo = AppSettingsRepository(path)
    with caplog.at_level(logging.WARNING, logger="app.settings_store"):
        s = repo.load()
    assert s.skills_repo_url is None  # falls back to defaults
    assert "settings.json" in caplog.text or "settings" in caplog.text.lower()


def test_save_null_url(tmp_path):
    path = tmp_path / "settings.json"
    repo = AppSettingsRepository(path)
    repo.save(AppSettings(skills_repo_url=None))
    loaded = repo.load()
    assert loaded.skills_repo_url is None
