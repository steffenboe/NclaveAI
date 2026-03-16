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
    mock_policy.evaluate.return_value = (True, None)
    return AgentWorkflow(
        planner=mock_planner,
        policy=mock_policy,
        executor=mock_executor,
    )


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
    workflow._policy.evaluate.return_value = (False, "Not permitted")
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
    workflow._policy.evaluate.return_value = (False, "Scale not allowed")
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
        _planner_output(status="done"),
    ]
    workflow._executor.run.return_value = _action_result()
    ctx = workflow.run("investigate pods", "run-10")
    assert ctx.final_message == "Test summary."
    workflow._planner.summarize.assert_called_once_with(ctx)


def test_workflow_survives_summarize_failure(workflow):
    workflow._planner.next_action.return_value = _planner_output(status="done")
    workflow._planner.summarize.side_effect = RuntimeError("LLM unavailable")
    ctx = workflow.run("diagnose", "run-11")
    assert ctx.status == "done"
    assert ctx.final_message is None
