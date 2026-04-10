from app.config import Settings


def test_runs_file_default():
    s = Settings(policy_path="/tmp/policy")
    assert str(s.runs_file) == "runs.json"


def test_settings_file_default():
    s = Settings(policy_path="/tmp/policy")
    assert str(s.settings_file) == "settings.json"


def test_skills_repo_url_not_on_settings():
    s = Settings(policy_path="/tmp/policy")
    assert not hasattr(s, "skills_repo_url")
