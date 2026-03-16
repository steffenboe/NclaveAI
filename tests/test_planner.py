import pytest
from unittest.mock import MagicMock

from app.models import ActionResult, Command, PlannerOutput, RunContext
from app.planner import Planner, _format_history


def _make_command(argv=None):
    return Command(argv=argv or ["kubectl", "get", "pods", "-n", "notesllm"], rationale="test")


def _make_result(argv=None, stdout="ok"):
    return ActionResult(
        command=_make_command(argv),
        allowed=True,
        stdout=stdout,
        stderr=None,
        exit_code=0,
    )


@pytest.fixture
def ctx_empty():
    return RunContext(run_id="1", prompt="Pod backend is crashing", history=[])


@pytest.fixture
def ctx_with_history():
    return RunContext(
        run_id="1",
        prompt="Pod backend is crashing",
        history=[_make_result(stdout="backend-xyz   0/1   CrashLoopBackOff   5   10m")],
    )


def test_planner_returns_planner_output(ctx_empty):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = PlannerOutput(
        status="action",
        command=_make_command(),
        summary="Reading pod status to assess situation",
    )
    planner = Planner.__new__(Planner)
    planner._chain = mock_chain
    result = planner.next_action(ctx_empty)
    assert isinstance(result, PlannerOutput)
    assert result.status == "action"
    assert result.command is not None
    mock_chain.invoke.assert_called_once()


def test_planner_can_return_done(ctx_with_history):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = PlannerOutput(
        status="done",
        command=None,
        summary="Pod restarted successfully, no further action needed",
    )
    planner = Planner.__new__(Planner)
    planner._chain = mock_chain
    result = planner.next_action(ctx_with_history)
    assert result.status == "done"
    assert result.command is None


def test_planner_passes_history_to_llm(ctx_with_history):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = PlannerOutput(
        status="done", command=None, summary="done"
    )
    planner = Planner.__new__(Planner)
    planner._chain = mock_chain
    planner.next_action(ctx_with_history)
    call_kwargs = mock_chain.invoke.call_args[0][0]
    assert "history_str" in call_kwargs
    assert "kubectl" in call_kwargs["history_str"]  # the fixture uses a kubectl argv
    assert "history" not in call_kwargs  # history key must NOT be passed (unused by template)


def test_format_history_uses_argv():
    result = _make_result(argv=["kubectl", "get", "pods", "-n", "notesllm"])
    formatted = _format_history([result])
    assert "kubectl" in formatted
    assert "get" in formatted
    # Must NOT contain old action= field format
    assert "action=" not in formatted


def test_format_history_empty():
    formatted = _format_history([])
    assert "first action" in formatted


def test_planner_summarize_returns_string(ctx_with_history):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = "The backend pod was crash-looping. Logs were retrieved and the situation assessed."
    planner = Planner.__new__(Planner)
    planner._summarize_chain = mock_chain
    result = planner.summarize(ctx_with_history)
    assert isinstance(result, str)
    assert len(result) > 0
    mock_chain.invoke.assert_called_once()


def test_planner_summarize_passes_context(ctx_with_history):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = "Done."
    planner = Planner.__new__(Planner)
    planner._summarize_chain = mock_chain
    ctx_with_history.status = "done"
    planner.summarize(ctx_with_history)
    call_kwargs = mock_chain.invoke.call_args[0][0]
    assert "prompt" in call_kwargs
    assert "status" in call_kwargs
    assert "history_str" in call_kwargs


def test_webhook_prompt_prefix_included_in_history_format():
    """Ensure webhook prompt is passed through to the planner chain unchanged."""
    from app.models import RunContext
    ctx = RunContext(
        run_id="w1",
        prompt="[WEBHOOK EVENT]\nReceived at: 2026-03-13T12:00:00Z\nPayload:\n{\"alertname\": \"PodCrashLooping\"}",
        source="webhook",
    )
    # The prompt must start with the sentinel
    assert ctx.prompt.startswith("[WEBHOOK EVENT]")
