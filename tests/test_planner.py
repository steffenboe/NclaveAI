import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.models import ActionResult, Command, PlannerOutput, RunContext
from app.planner import Planner, _format_history
from app.skills import Skill, SkillRepository
from app.main import app as fastapi_app


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


def test_planner_returns_planner_output(tmp_path, ctx_empty):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = PlannerOutput(
        status="action",
        command=_make_command(),
        summary="Reading pod status to assess situation",
    )
    planner = Planner.__new__(Planner)
    planner._chain = mock_chain
    planner._skill_repo = SkillRepository(tmp_path / "skills.json")
    result = planner.next_action(ctx_empty)
    assert isinstance(result, PlannerOutput)
    assert result.status == "action"
    assert result.command is not None
    mock_chain.invoke.assert_called_once()


def test_planner_can_return_done(tmp_path, ctx_with_history):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = PlannerOutput(
        status="done",
        command=None,
        summary="Pod restarted successfully, no further action needed",
    )
    planner = Planner.__new__(Planner)
    planner._chain = mock_chain
    planner._skill_repo = SkillRepository(tmp_path / "skills.json")
    result = planner.next_action(ctx_with_history)
    assert result.status == "done"
    assert result.command is None


def test_planner_passes_history_to_llm(tmp_path, ctx_with_history):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = PlannerOutput(
        status="done", command=None, summary="done"
    )
    planner = Planner.__new__(Planner)
    planner._chain = mock_chain
    planner._skill_repo = SkillRepository(tmp_path / "skills.json")
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


def _make_skill(name: str, description: str, enabled: bool = True) -> Skill:
    return Skill(
        id="test-id",
        name=name,
        description=description,
        enabled=enabled,
        created_at=datetime.now(timezone.utc),
    )


def _repo_with_skills(tmp_path, skills: list[Skill]) -> SkillRepository:
    repo = SkillRepository(tmp_path / "skills.json")
    for s in skills:
        repo.create(name=s.name, description=s.description, enabled=s.enabled)
    return repo


def test_system_prompt_includes_skill_name(tmp_path):
    repo = _repo_with_skills(tmp_path, [_make_skill("kubectl", "Kubernetes CLI")])
    planner = Planner.__new__(Planner)
    planner._skill_repo = repo
    prompt = planner._build_system_prompt()
    assert "[kubectl]" in prompt
    assert "Kubernetes CLI" in prompt


def test_system_prompt_includes_multiple_skills(tmp_path):
    repo = _repo_with_skills(tmp_path, [
        _make_skill("kubectl", "k8s cli"),
        _make_skill("gh", "GitHub CLI"),
    ])
    planner = Planner.__new__(Planner)
    planner._skill_repo = repo
    prompt = planner._build_system_prompt()
    assert "[kubectl]" in prompt
    assert "[gh]" in prompt


def test_system_prompt_excludes_disabled_skills(tmp_path):
    repo = _repo_with_skills(tmp_path, [
        _make_skill("kubectl", "k8s cli", enabled=True),
        _make_skill("gh", "GitHub CLI", enabled=False),
    ])
    planner = Planner.__new__(Planner)
    planner._skill_repo = repo
    prompt = planner._build_system_prompt()
    assert "[kubectl]" in prompt
    assert "[gh]" not in prompt


def test_system_prompt_no_tools_fallback(tmp_path):
    repo = SkillRepository(tmp_path / "skills.json")  # empty
    planner = Planner.__new__(Planner)
    planner._skill_repo = repo
    prompt = planner._build_system_prompt()
    assert "No specific tools" in prompt


def test_next_action_passes_system_prompt_to_chain(tmp_path, ctx_empty):
    repo = _repo_with_skills(tmp_path, [_make_skill("kubectl", "k8s cli")])
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = PlannerOutput(
        status="done", command=None, summary="done"
    )
    planner = Planner.__new__(Planner)
    planner._chain = mock_chain
    planner._skill_repo = repo
    planner.next_action(ctx_empty)
    call_kwargs = mock_chain.invoke.call_args[0][0]
    assert "system_prompt" in call_kwargs
    assert "[kubectl]" in call_kwargs["system_prompt"]


def test_generate_policy_returns_string():
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = 'allow { input.argv[0] == "kubectl" }'
    planner = Planner.__new__(Planner)
    planner._policy_chain = mock_chain
    result = planner.generate_policy(
        skill_name="kubectl",
        skill_description="Kubernetes CLI",
        plain_description="only allow kubectl commands",
    )
    assert isinstance(result, str)
    assert result == 'allow { input.argv[0] == "kubectl" }'


def test_generate_policy_passes_all_context_to_chain():
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = "allow { true }"
    planner = Planner.__new__(Planner)
    planner._policy_chain = mock_chain
    planner.generate_policy(
        skill_name="gh",
        skill_description="GitHub CLI",
        plain_description="allow all gh commands",
    )
    call_kwargs = mock_chain.invoke.call_args[0][0]
    assert call_kwargs["skill_name"] == "gh"
    assert call_kwargs["skill_description"] == "GitHub CLI"
    assert call_kwargs["plain_description"] == "allow all gh commands"


def test_api_generate_policy_returns_policy_string(tmp_path):
    fastapi_app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    mock_planner = MagicMock()
    mock_planner.generate_policy.return_value = 'allow { input.argv[0] == "kubectl" }'

    with patch("app.main.Planner", return_value=mock_planner):
        client = TestClient(fastapi_app)
        res = client.post("/api/skills/generate-policy", json={
            "skill_name": "kubectl",
            "skill_description": "Kubernetes CLI",
            "description": "allow only kubectl commands",
        })

    assert res.status_code == 200
    body = res.json()
    assert "policy" in body
    assert body["policy"] == 'allow { input.argv[0] == "kubectl" }'


def test_api_generate_policy_missing_field_returns_422():
    client = TestClient(fastapi_app)
    res = client.post("/api/skills/generate-policy", json={
        "skill_name": "kubectl",
        # missing skill_description and description
    })
    assert res.status_code == 422
