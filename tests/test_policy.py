from pathlib import Path
import pytest

from pydantic import ValidationError

from app.config import Settings, settings
from app.models import Command
from app.policy import PolicyEvaluator

_REGO_PATH = Path(__file__).parent.parent / "policies" / "executor.rego"


@pytest.fixture(autouse=True)
def patch_policy_path(monkeypatch):
    monkeypatch.setattr(settings, "policy_path", _REGO_PATH)


def test_settings_requires_policy_path(monkeypatch):
    monkeypatch.delenv("POLICY_PATH", raising=False)
    # _env_file=None prevents pydantic-settings from loading a .env file
    # that could supply POLICY_PATH and mask the missing-var error
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def _cmd(argv: list[str]) -> Command:
    return Command(argv=argv, rationale="test")


@pytest.fixture
def observer():
    return PolicyEvaluator(roles=["INFRA_OBSERVER"])


@pytest.fixture
def operator():
    return PolicyEvaluator(roles=["INFRA_OPERATOR"])


# --- INFRA_OBSERVER ---

def test_observer_can_kubectl_get(observer):
    allowed, reason = observer.evaluate(_cmd(["kubectl", "get", "pods", "-n", "notesllm"]))
    assert allowed is True
    assert reason is None


def test_observer_can_kubectl_logs(observer):
    allowed, reason = observer.evaluate(_cmd(["kubectl", "logs", "backend-xyz", "-n", "notesllm"]))
    assert allowed is True


def test_observer_can_kubectl_describe(observer):
    allowed, reason = observer.evaluate(_cmd(["kubectl", "describe", "pod", "backend-xyz"]))
    assert allowed is True


def test_observer_can_helm_list(observer):
    allowed, reason = observer.evaluate(_cmd(["helm", "list", "-n", "notesllm"]))
    assert allowed is True


def test_observer_cannot_kubectl_rollout(observer):
    allowed, reason = observer.evaluate(_cmd(["kubectl", "rollout", "restart", "deployment/backend"]))
    assert allowed is False
    assert reason is not None


def test_observer_cannot_kubectl_scale(observer):
    allowed, reason = observer.evaluate(_cmd(["kubectl", "scale", "deployment/backend", "--replicas=3"]))
    assert allowed is False


def test_observer_cannot_helm_upgrade(observer):
    allowed, reason = observer.evaluate(_cmd(["helm", "upgrade", "notesllm", "./chart"]))
    assert allowed is False


# --- INFRA_OPERATOR ---

def test_operator_can_kubectl_rollout(operator):
    allowed, reason = operator.evaluate(_cmd(["kubectl", "rollout", "restart", "deployment/backend"]))
    assert allowed is True


def test_operator_can_kubectl_scale(operator):
    allowed, reason = operator.evaluate(_cmd(["kubectl", "scale", "deployment/backend", "--replicas=3"]))
    assert allowed is True


def test_operator_can_helm_upgrade(operator):
    allowed, reason = operator.evaluate(_cmd(["helm", "upgrade", "notesllm", "./chart"]))
    assert allowed is True


# --- edge cases ---

def test_no_roles_denies_everything():
    evaluator = PolicyEvaluator(roles=[])
    allowed, reason = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False


def test_unknown_command_denied():
    evaluator = PolicyEvaluator(roles=["INFRA_OPERATOR"])
    allowed, reason = evaluator.evaluate(_cmd(["rm", "-rf", "/"]))
    assert allowed is False


def test_observer_can_helm_status(observer):
    allowed, reason = observer.evaluate(_cmd(["helm", "status", "notesllm", "-n", "notesllm"]))
    assert allowed is True


def test_partial_argv_denied(observer):
    # ["kubectl"] alone (no subcommand) must be denied — no prefix of length 1 is in the allowlist
    allowed, reason = observer.evaluate(_cmd(["kubectl"]))
    assert allowed is False


# --- INFRA_OBSERVER secrets restriction ---

def test_observer_cannot_get_secrets(observer):
    allowed, reason = observer.evaluate(_cmd(["kubectl", "get", "secrets", "-n", "notesllm"]))
    assert allowed is False
    assert reason is not None


def test_observer_cannot_get_secret_by_name(observer):
    allowed, reason = observer.evaluate(_cmd(["kubectl", "get", "secret", "agent-secret", "-n", "notesllm"]))
    assert allowed is False


def test_observer_cannot_describe_secret(observer):
    allowed, reason = observer.evaluate(_cmd(["kubectl", "describe", "secret", "agent-secret"]))
    assert allowed is False


def test_operator_can_get_secrets(operator):
    # INFRA_OPERATOR retains secret access
    allowed, reason = operator.evaluate(_cmd(["kubectl", "get", "secrets", "-n", "notesllm"]))
    assert allowed is True


def test_multi_role_union_allows_write(observer):
    # A user with both roles should get operator privileges
    evaluator = PolicyEvaluator(roles=["INFRA_OBSERVER", "INFRA_OPERATOR"])
    allowed, reason = evaluator.evaluate(_cmd(["kubectl", "rollout", "restart", "deployment/backend"]))
    assert allowed is True


def test_policy_evaluator_raises_on_missing_file(monkeypatch):
    monkeypatch.setattr(settings, "policy_path", Path("/nonexistent/path/policy.rego"))
    with pytest.raises(FileNotFoundError):
        PolicyEvaluator(roles=["INFRA_OBSERVER"])
