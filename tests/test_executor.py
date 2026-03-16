from unittest.mock import MagicMock, patch

import pytest

from app.executor import CommandExecutor
from app.models import ActionResult, Command


def _make_command(argv: list[str]) -> Command:
    return Command(argv=argv, rationale="test")


def _mock_proc(stdout="ok", stderr="", returncode=0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


@pytest.mark.parametrize("argv", [
    ["kubectl", "get", "pods", "-n", "notesllm"],
    ["helm", "list", "-n", "notesllm"],
    ["kubectl", "rollout", "restart", "deployment/backend", "-n", "notesllm"],
    ["kubectl", "scale", "deployment/backend", "--replicas=3", "-n", "notesllm"],
    ["kubectl", "logs", "backend-pod-xyz", "-n", "notesllm"],
])
@patch("app.executor.subprocess.run")
def test_executor_passes_argv_unchanged_to_subprocess(mock_run, argv):
    mock_run.return_value = _mock_proc(stdout="output")
    result = CommandExecutor().run(_make_command(argv))
    assert mock_run.call_args[0][0] == argv
    assert result.exit_code == 0
    assert result.stdout == "output"
    assert result.allowed is True


@patch("app.executor.subprocess.run")
def test_executor_returns_stderr_on_failure(mock_run):
    mock_run.return_value = _mock_proc(stdout="", stderr="not found", returncode=1)
    result = CommandExecutor().run(_make_command(["kubectl", "get", "pods"]))
    assert result.exit_code == 1
    assert result.stderr == "not found"
    assert result.stdout is None


@patch("app.executor.subprocess.run")
def test_executor_strips_whitespace(mock_run):
    mock_run.return_value = _mock_proc(stdout="  pod list  ", stderr="  ")
    result = CommandExecutor().run(_make_command(["kubectl", "get", "pods"]))
    assert result.stdout == "pod list"
    assert result.stderr is None


def test_executor_takes_no_constructor_args():
    # CommandExecutor() must work without namespace or any other argument
    executor = CommandExecutor()
    assert executor is not None
