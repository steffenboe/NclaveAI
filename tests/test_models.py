from app.models import Command, PlannerOutput, ActionResult, RunContext


def test_command_requires_argv_and_rationale():
    cmd = Command(argv=["kubectl", "get", "pods", "-n", "notesllm"], rationale="check pods")
    assert cmd.argv == ["kubectl", "get", "pods", "-n", "notesllm"]
    assert cmd.rationale == "check pods"


def test_planner_output_action_has_command():
    cmd = Command(argv=["kubectl", "get", "pods"], rationale="r")
    out = PlannerOutput(status="action", command=cmd, summary="checking")
    assert out.command is cmd
    assert out.status == "action"


def test_planner_output_done_has_no_command():
    out = PlannerOutput(status="done", command=None, summary="all good")
    assert out.command is None


def test_action_result_has_command():
    cmd = Command(argv=["kubectl", "get", "pods"], rationale="r")
    result = ActionResult(command=cmd, allowed=True, exit_code=0)
    assert result.command is cmd


def test_run_context_defaults():
    ctx = RunContext(run_id="abc", prompt="fix pod")
    assert ctx.history == []
    assert ctx.status == "running"


def test_infra_action_and_execution_plan_do_not_exist():
    import app.models as m
    assert not hasattr(m, "InfraAction")
    assert not hasattr(m, "ExecutionPlan")


def test_command_rejects_empty_argv():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Command(argv=[], rationale="empty argv should be rejected")


