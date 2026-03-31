from app.config import Settings


def test_runs_file_default():
    s = Settings(policy_path="/tmp/policy")
    assert str(s.runs_file) == "runs.json"
