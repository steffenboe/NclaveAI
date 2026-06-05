from datetime import datetime, timezone

from app.models import Command
from app.policy import PolicyEvaluator
from app.skills import Skill


def _cmd(argv: list[str]) -> Command:
    return Command(argv=argv, rationale="test")


def _skill(policy: str | None) -> Skill:
    return Skill(
        id="test-id",
        name="test",
        description="test skill",
        policy=policy,
        created_at=datetime.now(timezone.utc),
    )


# ── default deny behaviour ────────────────────────────────────────────────────

def test_no_skills_denies_any_command():
    """With no skills at all, every command is denied by default."""
    evaluator = PolicyEvaluator()
    allowed, reason, skill = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False
    assert reason is not None
    assert skill is None


def test_no_skills_denies_multiple_commands():
    evaluator = PolicyEvaluator()
    for argv in [["gh", "pr", "list"], ["terraform", "plan"], ["rm", "-rf", "/"]]:
        allowed, _, _sn = evaluator.evaluate(_cmd(argv))
        assert allowed is False


def test_no_skill_policy_denies_command():
    """A skill with policy=None does not contribute any allow rule — command is denied."""
    evaluator = PolicyEvaluator(skills=[_skill(None)])
    allowed, reason, skill = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False
    assert reason is not None
    assert skill is None


def test_no_skills_list_denies_command():
    """An empty skills list means everything is denied."""
    evaluator = PolicyEvaluator(skills=[])
    allowed, reason, skill = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False
    assert reason is not None
    assert skill is None


# ── skill-level policy tests ──────────────────────────────────────────────────

def test_evaluator_returns_none_reason_when_allowed():
    evaluator = PolicyEvaluator(skills=[_skill("allow { true }")])
    allowed, reason, skill = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert reason is None


def test_skill_policy_allows_matching_command():
    evaluator = PolicyEvaluator(
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason, skill = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert reason is None
    assert skill.name == "test"


def test_skill_policy_non_matching_denies_command():
    """Skill only allows kubectl; cmd is gh — denied (no fallback)."""
    evaluator = PolicyEvaluator(
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason, skill = evaluator.evaluate(_cmd(["gh", "pr", "list"]))
    assert allowed is False
    assert reason is not None
    assert skill is None


def test_multi_skill_or_semantics():
    evaluator = PolicyEvaluator(
        skills=[
            _skill('allow {\n  input.argv[0] == "kubectl"\n}'),
            _skill('allow {\n  input.argv[0] == "gh"\n}'),
        ],
    )
    allowed, reason, skill = evaluator.evaluate(_cmd(["gh", "pr", "list"]))
    assert allowed is True
    assert reason is None


def test_deny_when_no_skill_matches():
    """Skill only allows kubectl; cmd is gh — denied."""
    evaluator = PolicyEvaluator(
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason, skill = evaluator.evaluate(_cmd(["gh", "pr", "list"]))
    assert allowed is False
    assert "denied" in reason
    assert skill is None


def test_skill_allow_works_without_global_policy():
    """A skill that allows kubectl works — no global policy needed."""
    evaluator = PolicyEvaluator(
        skills=[_skill('allow {\n  input.argv[0] == "kubectl"\n}')],
    )
    allowed, reason, skill = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert skill.name == "test"


def test_allow_all_skill():
    """A skill with 'allow { true }' allows any command."""
    evaluator = PolicyEvaluator(skills=[_skill("allow { true }")])
    allowed, _, skill = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert skill.name == "test"


# ── skill_overrides tests ─────────────────────────────────────────────────────

def _skill_with_id(skill_id: str, policy: str | None, enabled: bool = True) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        description="test skill",
        enabled=enabled,
        policy=policy,
        created_at=datetime.now(timezone.utc),
    )


def test_override_enables_globally_disabled_skill():
    """A globally-disabled skill can be enabled for a specific run via overrides."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=False)
    evaluator = PolicyEvaluator(skills=[skill])
    # Without override: globally disabled → denied
    allowed, _, _ = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False

    # With override enabling it: must now allow
    allowed, _, matched_skill = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides={"s1": True},
    )
    assert allowed is True
    assert matched_skill.name == "s1"


def test_override_disables_globally_enabled_skill():
    """A globally-enabled skill can be disabled for a specific run via overrides."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=True)
    evaluator = PolicyEvaluator(skills=[skill])
    # Without override: globally enabled → allows
    allowed, _, matched_skill = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert matched_skill.name == "s1"

    # With override disabling it: denied (no other skill, no global fallback)
    allowed, reason, matched_skill = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides={"s1": False},
    )
    assert allowed is False
    assert reason is not None
    assert matched_skill is None


def test_unknown_skill_id_in_overrides_is_ignored():
    """Unknown skill IDs in the overrides map must not cause errors."""
    evaluator = PolicyEvaluator()  # no skills
    allowed, _, _ = evaluator.evaluate(
        _cmd(["ls"]),
        skill_overrides={"nonexistent-id": True},
    )
    # No skills → denied by default
    assert allowed is False


def test_no_overrides_falls_back_to_global_enabled_flag():
    """When skill_overrides is None, global enabled flag is used."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=True)
    evaluator = PolicyEvaluator(skills=[skill])
    allowed, _, matched_skill = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides=None,
    )
    assert allowed is True
    assert matched_skill.name == "s1"


# ── remote skill tests ────────────────────────────────────────────────────────

def _remote_skill_with_id(skill_id: str, policy: str | None, enabled: bool = True) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        description="remote test skill",
        enabled=enabled,
        policy=policy,
        created_at=datetime.now(timezone.utc),
        source="remote",
    )


def test_remote_skill_ignores_override_disable():
    """Remote skills cannot be disabled via skill_overrides — they are always effective."""
    skill = _remote_skill_with_id("rs1", 'allow { input.argv[0] == "kubectl" }', enabled=True)
    evaluator = PolicyEvaluator(skills=[skill])
    # Override tries to disable the remote skill — must be ignored
    allowed, _, matched_skill = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides={"rs1": False},
    )
    assert allowed is True
    assert matched_skill.name == "rs1"


def test_remote_skill_always_enabled_even_if_yaml_says_disabled():
    """Even if a remote skill's YAML has enabled=false, it is still treated as enabled."""
    skill = _remote_skill_with_id("rs2", 'allow { input.argv[0] == "kubectl" }', enabled=False)
    evaluator = PolicyEvaluator(skills=[skill])
    allowed, _, matched_skill = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides={},
    )
    assert allowed is True
    assert matched_skill.name == "rs2"
