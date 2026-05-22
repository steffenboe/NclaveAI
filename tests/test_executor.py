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


# ── env injection + ${VAR} resolution tests ───────────────────────────────────


@patch("app.executor.subprocess.run")
def test_executor_resolves_env_vars_in_argv(mock_run):
    mock_run.return_value = _mock_proc(stdout="ok")
    env = {"API_TOKEN": "Bearer secret123"}
    result = CommandExecutor().run(
        _make_command(["curl", "-H", "Authorization: ${API_TOKEN}", "https://api.example.com"]),
        env=env,
    )
    # The actual subprocess should receive the resolved argv
    called_argv = mock_run.call_args[0][0]
    assert called_argv == ["curl", "-H", "Authorization: Bearer secret123", "https://api.example.com"]
    assert result.exit_code == 0


@patch("app.executor.subprocess.run")
def test_executor_leaves_unknown_vars_unresolved(mock_run):
    mock_run.return_value = _mock_proc(stdout="ok")
    env = {"KNOWN": "value"}
    CommandExecutor().run(
        _make_command(["echo", "${KNOWN}", "${UNKNOWN}"]),
        env=env,
    )
    called_argv = mock_run.call_args[0][0]
    assert called_argv == ["echo", "value", "${UNKNOWN}"]


@patch("app.executor.subprocess.run")
def test_executor_passes_env_to_subprocess(mock_run):
    mock_run.return_value = _mock_proc(stdout="ok")
    env = {"API_TOKEN": "secret"}
    CommandExecutor().run(_make_command(["curl", "https://example.com"]), env=env)
    call_kwargs = mock_run.call_args[1]
    assert "env" in call_kwargs
    assert call_kwargs["env"]["API_TOKEN"] == "secret"


@patch("app.executor.subprocess.run")
def test_executor_no_env_does_not_pass_env_to_subprocess(mock_run):
    mock_run.return_value = _mock_proc(stdout="ok")
    CommandExecutor().run(_make_command(["ls"]))
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs.get("env") is None


@patch("app.executor.subprocess.run")
def test_executor_command_model_retains_original_argv(mock_run):
    """The ActionResult.command should keep the original argv with placeholders."""
    mock_run.return_value = _mock_proc(stdout="ok")
    cmd = _make_command(["curl", "-H", "${TOKEN}", "https://api.example.com"])
    result = CommandExecutor().run(cmd, env={"TOKEN": "secret"})
    # Original command in result still has placeholder
    assert result.command.argv == ["curl", "-H", "${TOKEN}", "https://api.example.com"]
