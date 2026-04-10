from app.config import Settings


def test_runs_file_default():
    s = Settings(policy_path="/tmp/policy")
    assert str(s.runs_file) == "runs.json"


def test_skills_repo_url_defaults_to_none(monkeypatch):
    monkeypatch.delenv("SKILLS_REPO_URL", raising=False)
    monkeypatch.delenv("SKILLS_REPO_BRANCH", raising=False)
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    assert cfg.settings.skills_repo_url is None


def test_skills_repo_branch_defaults_to_main(monkeypatch):
    monkeypatch.delenv("SKILLS_REPO_URL", raising=False)
    monkeypatch.delenv("SKILLS_REPO_BRANCH", raising=False)
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    assert cfg.settings.skills_repo_branch == "main"
