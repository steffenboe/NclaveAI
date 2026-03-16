import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models import RunContext


@pytest.fixture
def client():
    from app.main import app, _runs, _events, _active_fingerprints
    _runs.clear()
    _events.clear()
    _active_fingerprints.clear()
    return TestClient(app)


def test_webhook_post_returns_queued(client):
    payload = {"alertname": "PodCrashLooping", "namespace": "production"}
    with patch("app.main._executor") as mock_exec:
        mock_exec.submit.return_value = None
        resp = client.post("/api/agent/webhook", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert "event_id" in body
    assert "run_id" in body


def test_webhook_deduplication(client):
    payload = {"alertname": "PodCrashLooping"}
    with patch("app.main._executor") as mock_exec:
        mock_exec.submit.return_value = None
        resp1 = client.post("/api/agent/webhook", json=payload)
        run_id_1 = resp1.json()["run_id"]
        fp = resp1.json()["fingerprint"]

        # Simulate run still active by inserting fingerprint as active
        from app.main import _active_fingerprints
        _active_fingerprints[fp] = run_id_1

        resp2 = client.post("/api/agent/webhook", json=payload)

    assert resp2.json()["status"] == "skipped"
    assert resp2.json()["run_id"] == run_id_1


def test_list_webhook_events(client):
    payload = {"foo": "bar"}
    with patch("app.main._executor") as mock_exec:
        mock_exec.submit.return_value = None
        client.post("/api/agent/webhook", json=payload)
    resp = client.get("/api/agent/webhooks")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["raw_payload"] == payload


def test_get_single_webhook_event(client):
    payload = {"test": 1}
    with patch("app.main._executor") as mock_exec:
        mock_exec.submit.return_value = None
        post_resp = client.post("/api/agent/webhook", json=payload)
    event_id = post_resp.json()["event_id"]
    resp = client.get(f"/api/agent/webhooks/{event_id}")
    assert resp.status_code == 200
    assert resp.json()["event_id"] == event_id


def test_get_unknown_webhook_event_returns_404(client):
    resp = client.get("/api/agent/webhooks/does-not-exist")
    assert resp.status_code == 404
