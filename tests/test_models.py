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
    assert ctx.history_start_index == 0
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


def test_run_context_parent_run_id_defaults_to_none():
    ctx = RunContext(run_id="child", prompt="follow up")
    assert ctx.parent_run_id is None


def test_run_context_parent_run_id_can_be_set():
    ctx = RunContext(run_id="child", prompt="follow up", parent_run_id="parent-123")
    assert ctx.parent_run_id == "parent-123"


def test_run_context_has_empty_skill_overrides_by_default():
    ctx = RunContext(run_id="r1", prompt="hello")
    assert ctx.skill_overrides == {}


def test_run_context_skill_overrides_stores_bool_by_skill_id():
    ctx = RunContext(run_id="r1", prompt="hello", skill_overrides={"skill-1": False, "skill-2": True})
    assert ctx.skill_overrides["skill-1"] is False
    assert ctx.skill_overrides["skill-2"] is True


def test_run_context_skill_overrides_serializes_to_json():
    ctx = RunContext(run_id="r1", prompt="hello", skill_overrides={"abc": True})
    data = ctx.model_dump()
    assert data["skill_overrides"] == {"abc": True}


def test_run_context_history_start_index_serializes_to_json():
    ctx = RunContext(run_id="r1", prompt="hello", history_start_index=2)
    data = ctx.model_dump()
    assert data["history_start_index"] == 2


