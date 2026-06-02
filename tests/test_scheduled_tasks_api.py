from __future__ import annotations


def test_user_can_create_and_list_own_scheduled_task(user_client):
    me = user_client.get("/api/auth/me").json()

    create = user_client.post(
        "/api/scheduled-tasks",
        json={"prompt": "check pods", "cron": "*/5 * * * *", "timezone": "UTC"},
    )
    assert create.status_code == 201
    task = create.json()
    assert task["owner_id"] == me["user_id"]
    assert task["prompt"] == "check pods"

    listed = user_client.get("/api/scheduled-tasks")
    assert listed.status_code == 200
    ids = [t["task_id"] for t in listed.json()]
    assert task["task_id"] in ids


def test_user_cannot_read_other_users_scheduled_task(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "Alice123!"})
    created = client.post(
        "/api/scheduled-tasks",
        json={"prompt": "alice task", "cron": "*/5 * * * *", "timezone": "UTC"},
    )
    assert created.status_code == 201
    task_id = created.json()["task_id"]

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "bob", "password": "Bob123!"})
    denied = client.get(f"/api/scheduled-tasks/{task_id}")
    assert denied.status_code == 404


def test_admin_cannot_list_other_users_scheduled_tasks(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "Alice123!"})
    created = client.post(
        "/api/scheduled-tasks",
        json={"prompt": "alice task", "cron": "*/5 * * * *", "timezone": "UTC"},
    )
    assert created.status_code == 201

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "admin", "password": "Admin123!"})
    listed = client.get("/api/scheduled-tasks")
    assert listed.status_code == 200
    assert not any(t["task_id"] == created.json()["task_id"] for t in listed.json())


def test_invalid_cron_returns_422(user_client):
    resp = user_client.post(
        "/api/scheduled-tasks",
        json={"prompt": "x", "cron": "not-a-cron", "timezone": "UTC"},
    )
    assert resp.status_code == 422


def test_patch_can_disable_scheduled_task(user_client):
    created = user_client.post(
        "/api/scheduled-tasks",
        json={"prompt": "x", "cron": "*/5 * * * *", "timezone": "UTC"},
    )
    task_id = created.json()["task_id"]

    patched = user_client.patch(
        f"/api/scheduled-tasks/{task_id}",
        json={"enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["next_run_at"] is None


def test_delete_scheduled_task(user_client):
    created = user_client.post(
        "/api/scheduled-tasks",
        json={"prompt": "x", "cron": "*/5 * * * *", "timezone": "UTC"},
    )
    task_id = created.json()["task_id"]

    deleted = user_client.delete(f"/api/scheduled-tasks/{task_id}")
    assert deleted.status_code == 204

    missing = user_client.get(f"/api/scheduled-tasks/{task_id}")
    assert missing.status_code == 404


def test_run_scheduled_task_now(user_client):
    created = user_client.post(
        "/api/scheduled-tasks",
        json={"prompt": "x", "cron": "*/5 * * * *", "timezone": "UTC"},
    )
    task_id = created.json()["task_id"]

    trigger = user_client.post(f"/api/scheduled-tasks/{task_id}/run")
    assert trigger.status_code == 202
    data = trigger.json()
    assert data["status"] == "running"
    assert "run_id" in data

    task = user_client.get(f"/api/scheduled-tasks/{task_id}")
    assert task.status_code == 200
    assert task.json()["last_run_id"] == data["run_id"]


def test_run_scheduled_task_now_forbidden_to_other_user(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "Alice123!"})
    created = client.post(
        "/api/scheduled-tasks",
        json={"prompt": "alice task", "cron": "*/5 * * * *", "timezone": "UTC"},
    )
    task_id = created.json()["task_id"]

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "bob", "password": "Bob123!"})
    denied = client.post(f"/api/scheduled-tasks/{task_id}/run")
    assert denied.status_code == 404
