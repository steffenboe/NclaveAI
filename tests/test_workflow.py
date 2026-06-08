from unittest.mock import MagicMock, patch
import pytest

from pydantic import ValidationError

from app.models import ActionResult, Command, PlannerOutput, RunContext
from app.workflow import AgentWorkflow


def _cmd(argv=None):
    return Command(argv=argv or ["kubectl", "get", "pods", "-n", "notesllm"], rationale="test")


def _planner_output(status="action", argv=None):
    command = _cmd(argv) if status == "action" else None
    return PlannerOutput(status=status, command=command, summary="test summary")


def _action_result(argv=None, stdout="ok"):
    return ActionResult(
        command=_cmd(argv), allowed=True,
        stdout=stdout, stderr=None, exit_code=0,
    )


@pytest.fixture
def workflow():
    mock_planner = MagicMock()
    mock_planner.summarize.return_value = "Test summary."
    mock_executor = MagicMock()
    mock_policy = MagicMock()
    mock_policy.evaluate.return_value = (True, None, None)
    return AgentWorkflow(
        planner=mock_planner,
        policy=mock_policy,
        executor=mock_executor,
    )


@pytest.fixture
def mock_planner():
    m = MagicMock()
    m.summarize.return_value = "Test summary."
    return m


@pytest.fixture
def mock_executor():
    return MagicMock()


@pytest.fixture
def mock_policy():
    m = MagicMock()
    m.evaluate.return_value = (True, None, None)
    return m


@pytest.fixture
def mock_policy_deny():
    m = MagicMock()
    m.evaluate.return_value = (False, "Denied by policy", None)
    return m


def _make_audit_repo():
    from app.models import CommandPolicyEvaluated, CommandApprovalDecision, CommandExecutionFinished
    repo = MagicMock()
    repo.appended = []
    repo.append.side_effect = lambda e: repo.appended.append(e)
    return repo



def test_workflow_runs_single_action_then_done(workflow):
    workflow._planner.next_action.side_effect = [
        _planner_output(status="action"),
        _planner_output(status="done"),
    ]
    workflow._executor.run.return_value = _action_result()
    ctx = workflow.run("fix the pod", "run-1")
    assert ctx.status == "done"
    assert len(ctx.history) == 1
    workflow._executor.run.assert_called_once()


def test_workflow_stops_on_policy_denial(workflow):
    workflow._planner.next_action.return_value = _planner_output(
        status="action", argv=["kubectl", "rollout", "restart", "deployment/backend"]
    )
    workflow._policy.evaluate.return_value = (False, "Not permitted", None)
    ctx = workflow.run("restart backend", "run-2")
    assert ctx.status == "policy_denied"
    workflow._executor.run.assert_not_called()
    workflow._planner.next_action.assert_called_once()


def test_workflow_stops_when_planner_returns_failed(workflow):
    workflow._planner.next_action.return_value = _planner_output(status="failed")
    ctx = workflow.run("impossible task", "run-3")
    assert ctx.status == "failed"
    workflow._executor.run.assert_not_called()


def test_workflow_respects_max_iterations(workflow):
    workflow._planner.next_action.return_value = _planner_output(status="action")
    workflow._executor.run.return_value = _action_result()
    ctx = workflow.run("infinite loop", "run-4", max_iterations=3)
    assert len(ctx.history) == 3
    assert ctx.status == "failed"


def test_workflow_accumulates_history(workflow):
    workflow._planner.next_action.side_effect = [
        _planner_output(status="action", argv=["kubectl", "get", "pods"]),
        _planner_output(status="action", argv=["kubectl", "logs", "backend-xyz"]),
        _planner_output(status="done"),
    ]
    workflow._executor.run.side_effect = [
        _action_result(stdout="pod info"),
        _action_result(stdout="log output"),
    ]
    ctx = workflow.run("diagnose", "run-5")
    assert len(ctx.history) == 2
    assert ctx.history[0].stdout == "pod info"
    assert ctx.history[1].stdout == "log output"
    assert ctx.status == "done"


def test_workflow_does_not_execute_when_denied(workflow):
    workflow._planner.next_action.return_value = _planner_output(
        status="action", argv=["kubectl", "scale", "deployment/backend", "--replicas=3"]
    )
    workflow._policy.evaluate.return_value = (False, "Scale not allowed", None)
    ctx = workflow.run("scale up", "run-6")
    assert ctx.status == "policy_denied"
    assert len(ctx.history) == 0
    workflow._planner.next_action.assert_called_once()


def test_policy_receives_command_not_plan(workflow):
    """policy.evaluate must be called with a Command."""
    workflow._planner.next_action.side_effect = [
        _planner_output(status="action"),
        _planner_output(status="done"),
    ]
    workflow._executor.run.return_value = _action_result()
    workflow.run("fix", "run-7")
    call_arg = workflow._policy.evaluate.call_args[0][0]
    assert isinstance(call_arg, Command)


def test_executor_receives_command_not_plan(workflow):
    """executor.run must be called with a Command."""
    workflow._planner.next_action.side_effect = [
        _planner_output(status="action"),
        _planner_output(status="done"),
    ]
    workflow._executor.run.return_value = _action_result()
    workflow.run("fix", "run-8")
    call_arg = workflow._executor.run.call_args[0][0]
    assert isinstance(call_arg, Command)


def test_planner_output_action_with_null_command_is_rejected():
    """PlannerOutput(status='action', command=None) must be invalid."""
    with pytest.raises(ValidationError):
        PlannerOutput(status="action", command=None, summary="bad")


# --- API smoke tests ---

from fastapi.testclient import TestClient
from app.main import app as fastapi_app


def test_api_start_run_returns_202():
    with patch("app.main._build_workflow") as mock_build:
        mock_wf = MagicMock()
        mock_wf.run.return_value = RunContext(
            run_id="x", prompt="test", history=[], status="done"
        )
        mock_build.return_value = mock_wf
        from pathlib import Path
        from app.runs import RunRepository
        from app.skills import SkillRepository
        fastapi_app.state.run_repo = RunRepository(Path("/tmp/notesllm-test-runs.json"))
        fastapi_app.state.skill_repo = SkillRepository(Path("/tmp/notesllm-test-skills.json"))
        client = TestClient(fastapi_app)
        response = client.post("/api/agent/run", json={"prompt": "fix pod"})
    assert response.status_code == 202
    body = response.json()
    assert "run_id" in body
    assert body["status"] == "running"


def test_api_get_unknown_run_returns_404():
    client = TestClient(fastapi_app)
    response = client.get("/api/agent/runs/does-not-exist")
    assert response.status_code == 404


def test_health_endpoint():
    client = TestClient(fastapi_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_workflow_sets_final_message(workflow):
    workflow._planner.next_action.side_effect = [
        _planner_output(status="action"),
        PlannerOutput(status="done", command=None, summary="Test summary."),
    ]
    workflow._executor.run.return_value = _action_result()
    ctx = workflow.run("investigate pods", "run-10")
    assert ctx.final_message == "Test summary."
    workflow._planner.summarize.assert_not_called()


def test_workflow_done_uses_planner_summary_as_final_message(workflow):
    """When planner returns done, plan_output.summary becomes final_message directly."""
    workflow._planner.next_action.return_value = PlannerOutput(
        status="done", command=None, summary="Task completed successfully."
    )
    ctx = workflow.run("diagnose", "run-11a")
    assert ctx.status == "done"
    assert ctx.final_message == "Task completed successfully."
    workflow._planner.summarize.assert_not_called()


def test_workflow_done_final_message_never_null_even_if_summarize_raises(workflow):
    """done run must always have a non-null final_message — no blank UI turns."""
    workflow._planner.next_action.return_value = PlannerOutput(
        status="done", command=None, summary="Direct answer."
    )
    workflow._planner.summarize.side_effect = RuntimeError("LLM unavailable")
    ctx = workflow.run("diagnose", "run-11b")
    assert ctx.status == "done"
    assert ctx.final_message is not None


def test_workflow_survives_summarize_failure(workflow):
    # summarize() is no longer called for 'done' status — plan_output.summary is used directly.
    # This test verifies the run still completes successfully even if summarize were to raise.
    workflow._planner.next_action.return_value = PlannerOutput(
        status="done", command=None, summary="Fallback summary."
    )
    workflow._planner.summarize.side_effect = RuntimeError("LLM unavailable")
    ctx = workflow.run("diagnose", "run-11")
    assert ctx.status == "done"
    assert ctx.final_message == "Fallback summary."


def test_runcontext_accepts_waiting_approval_status():
    ctx = RunContext(run_id="r1", prompt="p")
    ctx.status = "waiting_approval"
    assert ctx.status == "waiting_approval"


def test_runcontext_pending_command_defaults_to_none():
    ctx = RunContext(run_id="r1", prompt="p")
    assert ctx.pending_command is None


def test_runcontext_pending_command_can_be_set():
    ctx = RunContext(run_id="r1", prompt="p")
    ctx.pending_command = {"argv": ["ls"], "rationale": "check"}
    assert ctx.pending_command["argv"] == ["ls"]


# ── Approval gate tests ────────────────────────────────────────────────────

def test_workflow_approval_gate_approve_allows_execution():
    """Gate returning True → command executes normally."""
    mock_planner = MagicMock()
    mock_planner.summarize.return_value = "done"
    mock_policy = MagicMock()
    mock_policy.evaluate.return_value = (True, None, None)
    mock_executor = MagicMock()
    mock_executor.run.return_value = _action_result()

    mock_planner.next_action.side_effect = [
        _planner_output(status="action"),
        _planner_output(status="done"),
    ]

    gate = MagicMock(return_value=True)
    wf = AgentWorkflow(
        planner=mock_planner,
        policy=mock_policy,
        executor=mock_executor,
        approval_gate=gate,
    )
    ctx = wf.run("fix pod", "run-gate-approve")
    assert ctx.status == "done"
    gate.assert_called_once()
    gate_call_arg = gate.call_args[0][0]
    assert isinstance(gate_call_arg, Command)
    mock_executor.run.assert_called_once()


def test_workflow_approval_gate_deny_stops_execution():
    """Gate returning False → policy_denied, executor never called."""
    mock_planner = MagicMock()
    mock_policy = MagicMock()
    mock_policy.evaluate.return_value = (True, None, None)
    mock_executor = MagicMock()

    mock_planner.next_action.return_value = _planner_output(status="action")

    gate = MagicMock(return_value=False)
    wf = AgentWorkflow(
        planner=mock_planner,
        policy=mock_policy,
        executor=mock_executor,
        approval_gate=gate,
    )
    ctx = wf.run("fix pod", "run-gate-deny")
    assert ctx.status == "policy_denied"
    gate.assert_called_once()
    mock_executor.run.assert_not_called()


def test_workflow_no_gate_executes_normally(workflow):
    """No gate → existing behaviour unchanged."""
    workflow._planner.next_action.side_effect = [
        _planner_output(status="action"),
        _planner_output(status="done"),
    ]
    workflow._executor.run.return_value = _action_result()
    ctx = workflow.run("fix pod", "run-no-gate")
    assert ctx.status == "done"
    assert len(ctx.history) == 1


def test_workflow_run_accepts_prebuilt_ctx():
    """Passing ctx= reuses the object and returns the same instance."""
    mock_planner = MagicMock()
    mock_planner.summarize.return_value = "done"
    mock_policy = MagicMock()
    mock_policy.evaluate.return_value = (True, None, None)
    mock_executor = MagicMock()
    mock_executor.run.return_value = _action_result()
    mock_planner.next_action.side_effect = [
        _planner_output(status="action"),
        _planner_output(status="done"),
    ]

    wf = AgentWorkflow(planner=mock_planner, policy=mock_policy, executor=mock_executor)
    ctx = RunContext(run_id="r-ctx", prompt="p")
    result = wf.run(prompt="p", ctx=ctx)
    assert result is ctx   # same object
    assert ctx.status == "done"


def test_workflow_policy_denied_sets_final_message_with_command(workflow):
    """policy_denied → final_message includes the denied command, summarize not called."""
    workflow._planner.next_action.return_value = _planner_output(status="action")
    workflow._policy.evaluate.return_value = (False, "Not permitted", None)
    ctx = workflow.run("fix", "run-denied-msg")
    assert ctx.status == "policy_denied"
    assert "kubectl get pods -n notesllm" in ctx.final_message
    assert ctx.final_message.startswith("Run stopped:")
    workflow._planner.summarize.assert_not_called()


def test_api_get_settings_returns_approval_required():
    client = TestClient(fastapi_app)
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert "approval_required" in body
    assert "llm_base_url" in body
    assert "has_llm_api_key" in body
    assert body["approval_required"] is False  # default


def test_api_put_settings_updates_approval_required():
    client = TestClient(fastapi_app)
    # turn on
    r = client.put("/api/settings", json={"approval_required": True})
    assert r.status_code == 200
    assert r.json()["approval_required"] is True
    # turn off again (clean up)
    client.put("/api/settings", json={"approval_required": False})


def test_api_put_settings_updates_llm_endpoint_and_token():
    client = TestClient(fastapi_app)
    endpoint = "https://example-llm.invalid/v1"
    response = client.put("/api/settings", json={
        "llm_base_url": endpoint,
        "llm_api_key": "test-token-123",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["llm_base_url"] == endpoint
    assert body["has_llm_api_key"] is True


def test_api_put_settings_rejects_empty_llm_endpoint():
    client = TestClient(fastapi_app)
    response = client.put("/api/settings", json={"llm_base_url": "   "})
    assert response.status_code == 422

def test_api_approve_unknown_run_returns_404():
    client = TestClient(fastapi_app)
    response = client.post("/api/agent/runs/does-not-exist/approve")
    assert response.status_code == 404


def test_api_deny_unknown_run_returns_404():
    client = TestClient(fastapi_app)
    response = client.post("/api/agent/runs/does-not-exist/deny")
    assert response.status_code == 404


def test_workflow_passes_skill_overrides_to_policy(mock_planner, mock_executor, mock_policy):
    """workflow must pass ctx.skill_overrides into policy.evaluate() on each call."""
    mock_planner.next_action.side_effect = [
        PlannerOutput(status="action", command=Command(argv=["kubectl", "get", "pods"], rationale="r"), summary="s"),
        PlannerOutput(status="done", summary="all done"),
    ]
    mock_policy.evaluate.return_value = (True, None, None)
    mock_executor.run.return_value = ActionResult(
        command=Command(argv=["kubectl", "get", "pods"], rationale="r"),
        allowed=True, stdout="", stderr="", exit_code=0,
    )

    ctx = RunContext(
        run_id="r1",
        prompt="test",
        skill_overrides={"skill-abc": False},
    )
    wf = AgentWorkflow(planner=mock_planner, policy=mock_policy, executor=mock_executor)
    wf.run(prompt="test", ctx=ctx)

    mock_policy.evaluate.assert_called_once_with(
        Command(argv=["kubectl", "get", "pods"], rationale="r"),
        skill_overrides={"skill-abc": False},
    )


def test_globally_enabled_skill_blocked_by_conversation_override(mock_planner, mock_executor):
    """An override disabling a globally-enabled skill blocks commands that skill would allow."""
    from app.policy import PolicyEvaluator
    from app.skills import Skill
    from datetime import datetime, timezone

    skill = Skill(
        id="k8s",
        name="kubectl",
        description="k8s",
        enabled=True,  # globally enabled
        policy='allow { input.argv[0] == "kubectl" }',
        created_at=datetime.now(timezone.utc),
    )
    policy = PolicyEvaluator(skills=[skill])

    mock_planner.next_action.return_value = PlannerOutput(
        status="action",
        command=Command(argv=["kubectl", "get", "pods"], rationale="check pods"),
        summary="running kubectl",
    )

    ctx = RunContext(
        run_id="r-override",
        prompt="check pods",
        skill_overrides={"k8s": False},  # override: disable kubectl skill for this run
    )

    wf = AgentWorkflow(planner=mock_planner, policy=policy, executor=mock_executor)
    result = wf.run(prompt="check pods", ctx=ctx)

    # No skill allows this command (skill disabled by override), so it is denied by default
    assert result.status == "policy_denied"
    mock_executor.run.assert_not_called()


# ── Secrets store integration ─────────────────────────────────────────────────

def test_workflow_injects_secrets_from_store_not_os_environ(mock_planner, mock_executor):
    """Secrets must come from SecretsStore, not os.environ."""
    from datetime import datetime, timezone
    from app.policy import PolicyEvaluator
    from app.secrets_store import SecretsStore
    from app.skills import Skill
    from pathlib import Path
    import tempfile, os

    # Create a skill with env var
    skill = Skill(
        id="curl-skill",
        name="curl",
        description="HTTP client",
        enabled=True,
        policy='allow { input.argv[0] == "curl" }',
        env=["API_TOKEN"],
        created_at=datetime.now(timezone.utc),
    )
    policy = PolicyEvaluator(skills=[skill])

    # Set up secrets store with the token
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write('{"API_TOKEN": "secret_from_store"}')
        secrets_path = Path(f.name)
    secrets_store = SecretsStore(secrets_path)

    # Ensure the token is NOT in os.environ
    os.environ.pop("API_TOKEN", None)

    mock_planner.next_action.side_effect = [
        PlannerOutput(
            status="action",
            command=Command(argv=["curl", "-H", "Authorization: ${API_TOKEN}", "https://api.example.com"], rationale="test"),
            summary="calling API",
        ),
        PlannerOutput(status="done", summary="done"),
    ]
    mock_executor.run.return_value = ActionResult(
        command=Command(argv=["curl", "-H", "Authorization: ${API_TOKEN}", "https://api.example.com"], rationale="test"),
        allowed=True, stdout="response", stderr="", exit_code=0,
    )

    wf = AgentWorkflow(
        planner=mock_planner, policy=policy, executor=mock_executor,
        secrets_store=secrets_store,
    )
    ctx = RunContext(run_id="r-secrets", prompt="call api")
    wf.run(prompt="call api", ctx=ctx)

    # Executor must have been called with env containing the secret
    call_kwargs = mock_executor.run.call_args
    env_arg = call_kwargs[1].get("env") if call_kwargs[1] else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
    assert env_arg == {"API_TOKEN": "secret_from_store"}

    secrets_path.unlink()


def test_workflow_no_secrets_store_passes_no_env(mock_planner, mock_executor):
    """Without a secrets store, no env is passed to executor."""
    from datetime import datetime, timezone
    from app.policy import PolicyEvaluator
    from app.skills import Skill

    skill = Skill(
        id="curl-skill",
        name="curl",
        description="HTTP client",
        enabled=True,
        policy='allow { input.argv[0] == "curl" }',
        env=["API_TOKEN"],
        created_at=datetime.now(timezone.utc),
    )
    policy = PolicyEvaluator(skills=[skill])

    mock_planner.next_action.side_effect = [
        PlannerOutput(
            status="action",
            command=Command(argv=["curl", "https://api.example.com"], rationale="test"),
            summary="calling API",
        ),
        PlannerOutput(status="done", summary="done"),
    ]
    mock_executor.run.return_value = ActionResult(
        command=Command(argv=["curl", "https://api.example.com"], rationale="test"),
        allowed=True, stdout="ok", stderr="", exit_code=0,
    )

    wf = AgentWorkflow(
        planner=mock_planner, policy=policy, executor=mock_executor,
        secrets_store=None,  # No secrets store
    )
    ctx = RunContext(run_id="r-no-secrets", prompt="call api")
    wf.run(prompt="call api", ctx=ctx)

    # Executor called with env=None
    call_kwargs = mock_executor.run.call_args
    env_arg = call_kwargs[1].get("env") if call_kwargs[1] else None
    assert env_arg is None


def test_workflow_emits_policy_and_execution_events_on_allow(
    mock_planner, mock_policy, mock_executor
):
    from app.workflow import AgentWorkflow
    from app.models import CommandPolicyEvaluated, CommandExecutionFinished

    audit_repo = _make_audit_repo()
    mock_planner.next_action.side_effect = [
        _planner_output(status="action"),
        _planner_output(status="done"),
    ]
    mock_executor.run.return_value = _action_result()
    
    wf = AgentWorkflow(
        planner=mock_planner,
        policy=mock_policy,
        executor=mock_executor,
        audit_repo=audit_repo,
    )
    wf.run(prompt="test", run_id="r1", max_iterations=1)

    types = [type(e).__name__ for e in audit_repo.appended]
    assert "CommandPolicyEvaluated" in types
    assert "CommandExecutionFinished" in types
    assert "CommandApprovalDecision" not in types

    policy_event = next(e for e in audit_repo.appended if isinstance(e, CommandPolicyEvaluated))
    exec_event = next(e for e in audit_repo.appended if isinstance(e, CommandExecutionFinished))
    assert policy_event.run_id == "r1"
    assert policy_event.allowed is True
    assert policy_event.command_id == exec_event.command_id


def test_workflow_emits_only_policy_event_on_policy_deny(
    mock_planner, mock_policy_deny, mock_executor
):
    from app.workflow import AgentWorkflow
    from app.models import CommandPolicyEvaluated

    audit_repo = _make_audit_repo()
    mock_planner.next_action.return_value = _planner_output(status="action")
    
    wf = AgentWorkflow(
        planner=mock_planner,
        policy=mock_policy_deny,
        executor=mock_executor,
        audit_repo=audit_repo,
    )
    wf.run(prompt="test", run_id="r1", max_iterations=1)

    types = [type(e).__name__ for e in audit_repo.appended]
    assert "CommandPolicyEvaluated" in types
    assert "CommandApprovalDecision" not in types
    assert "CommandExecutionFinished" not in types

    policy_event = next(e for e in audit_repo.appended if isinstance(e, CommandPolicyEvaluated))
    assert policy_event.allowed is False

