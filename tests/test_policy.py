from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.models import Command
from app.policy import PolicyEvaluator
from app.skills import Skill

_REGO_PATH = Path(__file__).parent.parent / "policies" / "executor.rego"


@pytest.fixture(autouse=True)
def patch_policy_path(monkeypatch):
    monkeypatch.setattr(settings, "policy_path", _REGO_PATH)


def test_settings_requires_policy_path(monkeypatch):
    monkeypatch.delenv("POLICY_PATH", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def _cmd(argv: list[str]) -> Command:
    return Command(argv=argv, rationale="test")


def test_deny_all_policy_denies_any_command():
    # The default executor.rego has `default allow = false`
    evaluator = PolicyEvaluator()
    allowed, reason = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False
    assert reason is not None


def test_deny_all_policy_denies_multiple_commands():
    evaluator = PolicyEvaluator()
    for argv in [["gh", "pr", "list"], ["terraform", "plan"], ["rm", "-rf", "/"]]:
        allowed, _ = evaluator.evaluate(_cmd(argv))
        assert allowed is False


def _allow_all_policy(tmp_path: Path) -> Path:
    p = tmp_path / "allow_all.rego"
    p.write_text("package ops.agent\ndefault allow = true\n")
    return p


def _skill(policy: str | None) -> Skill:
    return Skill(
        id="test-id",
        name="test",
        description="test skill",
        policy=policy,
        created_at=datetime.now(timezone.utc),
    )


def test_evaluator_returns_none_reason_when_allowed(tmp_path):
    # Write an allow-all policy and supply a skill that also allows all
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[_skill("allow { true }")],
    )
    allowed, reason = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert reason is None


def test_policy_evaluator_raises_on_missing_file():
    with pytest.raises(FileNotFoundError):
        PolicyEvaluator(policy_path=Path("/nonexistent/path/policy.rego"))


# ── skill-level policy tests ───────────────────────────────────────────────────

def test_skill_policy_allows_matching_command(tmp_path):
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert reason is None


def test_skill_policy_denies_non_matching_command(tmp_path):
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason = evaluator.evaluate(_cmd(["gh", "pr", "list"]))
    assert allowed is False
    assert reason == "No skill policy permits this command"


def test_no_skill_policy_denies_all(tmp_path):
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[_skill(None)],
    )
    allowed, reason = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False
    assert reason == "No skill policy permits this command"


def test_no_skills_denies_all(tmp_path):
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[],
    )
    allowed, reason = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False
    assert reason == "No skill policy permits this command"


def test_multi_skill_or_semantics(tmp_path):
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[
            _skill('allow {\n  input.argv[0] == "kubectl"\n}'),
            _skill('allow {\n  input.argv[0] == "gh"\n}'),
        ],
    )
    allowed, reason = evaluator.evaluate(_cmd(["gh", "pr", "list"]))
    assert allowed is True
    assert reason is None


def test_global_deny_takes_priority():
    # autouse fixture points at executor.rego which has `default allow = false`
    evaluator = PolicyEvaluator(
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False
    assert "denied by policy" in reason


def test_policy_path_defaults_to_settings():
    # autouse fixture sets settings.policy_path to a valid path;
    # calling with no policy_path must not raise
    evaluator = PolicyEvaluator(skills=[_skill("allow { true }")])
    allowed, _ = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    # executor.rego denies everything — just verifying no exception raised
    assert allowed is False
