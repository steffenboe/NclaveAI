import time
from unittest.mock import MagicMock, patch


def test_api_run_exposes_llm_failure_via_polling(client):
    expected_message = "LLM API authorization failed. Check the configured API key."

    def fail_run(*, ctx, **_kwargs):
        ctx.status = "failed"
        ctx.final_message = expected_message
        return ctx

    with patch("app.main._build_workflow") as mock_build:
        mock_wf = MagicMock()
        mock_wf.run.side_effect = fail_run
        mock_build.return_value = mock_wf

        response = client.post("/api/agent/run", json={"prompt": "fix pod"})
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        run = None
        for _ in range(20):
            poll = client.get(f"/api/agent/runs/{run_id}")
            assert poll.status_code == 200
            run = poll.json()
            if run["status"] == "failed":
                break
            time.sleep(0.01)

    assert run is not None
    assert run["status"] == "failed"
    assert run["final_message"] == expected_message
    mock_wf.run.assert_called_once()
