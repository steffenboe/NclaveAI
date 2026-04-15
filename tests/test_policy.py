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
    # The default executor.rego has `default allow = false`; no skills means global is used.
    evaluator = PolicyEvaluator()
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False
    assert reason is not None
    assert skill_name is None


def test_deny_all_policy_denies_multiple_commands():
    evaluator = PolicyEvaluator()
    for argv in [["gh", "pr", "list"], ["terraform", "plan"], ["rm", "-rf", "/"]]:
        allowed, _, _sn = evaluator.evaluate(_cmd(argv))
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
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[_skill("allow { true }")],
    )
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
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
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert reason is None
    assert skill_name == "test"


def test_skill_policy_non_matching_falls_through_to_global(tmp_path):
    # Skill only allows kubectl; cmd is gh — falls through to global allow-all.
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["gh", "pr", "list"]))
    assert allowed is True
    assert skill_name is None  # global fallback, no skill claimed it


def test_no_skill_policy_falls_through_to_global(tmp_path):
    # Skill has no policy (policy=None) — no skill interp is created; falls through to global.
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[_skill(None)],
    )
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert skill_name is None


def test_no_skills_falls_through_to_global(tmp_path):
    # No skills registered — always falls through to global.
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[],
    )
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert skill_name is None


def test_multi_skill_or_semantics(tmp_path):
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[
            _skill('allow {\n  input.argv[0] == "kubectl"\n}'),
            _skill('allow {\n  input.argv[0] == "gh"\n}'),
        ],
    )
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["gh", "pr", "list"]))
    assert allowed is True
    assert reason is None


def test_global_deny_applies_when_no_skill_matches():
    # executor.rego has `default allow = false`; skill only allows kubectl; cmd is gh.
    evaluator = PolicyEvaluator(
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["gh", "pr", "list"]))
    assert allowed is False
    assert "denied by policy" in reason
    assert skill_name is None


def test_skill_allow_takes_priority_over_global_deny():
    # executor.rego denies everything, but skill explicitly allows kubectl.
    evaluator = PolicyEvaluator(
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert skill_name == "test"


def test_policy_path_defaults_to_settings():
    # autouse fixture sets settings.policy_path to executor.rego;
    # skill "allow { true }" wins → no exception raised, command is allowed.
    evaluator = PolicyEvaluator(skills=[_skill("allow { true }")])
    allowed, _, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert skill_name == "test"


def _skill_with_id(skill_id: str, policy: str | None, enabled: bool = True) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        description="test skill",
        enabled=enabled,
        policy=policy,
        created_at=datetime.now(timezone.utc),
    )


# ── skill_overrides tests ──────────────────────────────────────────────────────

def test_override_enables_globally_disabled_skill():
    """A globally-disabled skill can be enabled for a specific run via overrides."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=False)
    evaluator = PolicyEvaluator(skills=[skill])
    # Without override: globally disabled → must not allow (falls through to global deny-all)
    allowed, _, _ = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False

    # With override enabling it: must now allow
    allowed, _, skill_name = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides={"s1": True},
    )
    assert allowed is True
    assert skill_name == "s1"


def test_override_disables_globally_enabled_skill(tmp_path):
    """A globally-enabled skill can be disabled for a specific run via overrides."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=True)
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[skill],
    )
    # Without override: globally enabled → allows
    allowed, _, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert skill_name == "s1"

    # With override disabling it: must fall through to global (allow-all in this test)
    allowed, _, skill_name = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides={"s1": False},
    )
    assert allowed is True   # falls through to global allow-all
    assert skill_name is None  # global fallback, not the skill


def test_unknown_skill_id_in_overrides_is_ignored():
    """Unknown skill IDs in the overrides map must not cause errors."""
    evaluator = PolicyEvaluator()  # no skills
    allowed, _, _ = evaluator.evaluate(
        _cmd(["ls"]),
        skill_overrides={"nonexistent-id": True},
    )
    # executor.rego is deny-all, so even with an unknown override the result is denied
    assert allowed is False


def test_no_overrides_falls_back_to_global_enabled_flag():
    """When skill_overrides is None, global enabled flag is used (existing behaviour)."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=True)
    evaluator = PolicyEvaluator(skills=[skill])
    allowed, _, skill_name = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides=None,
    )
    assert allowed is True
    assert skill_name == "s1"
