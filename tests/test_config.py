from app.config import Settings


def test_runs_file_default():
    s = Settings()
    assert str(s.runs_file) == "runs.json"


def test_settings_file_default():
    s = Settings()
    assert str(s.settings_file) == "settings.json"


def test_skills_repo_url_not_on_settings():
    s = Settings()
    assert not hasattr(s, "skills_repo_url")


def test_audit_file_default():
    from app.config import Settings
    s = Settings()
    assert str(s.audit_file) == "audit.jsonl"

