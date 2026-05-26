from __future__ import annotations


class TestLogin:
    def test_login_success_sets_cookie(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123!"})
        assert resp.status_code == 200
        assert "access_token" in client.cookies

    def test_login_returns_user_info(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123!"})
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
        assert "hashed_password" not in data

    def test_login_wrong_password(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_unknown_user(self, client):
        resp = client.post("/api/auth/login", json={"username": "nobody", "password": "pw"})
        assert resp.status_code == 401


class TestMe:
    def test_me_with_valid_cookie(self, admin_client):
        resp = admin_client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
        assert "hashed_password" not in data

    def test_me_without_cookie(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_user_role(self, user_client):
        resp = user_client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["role"] == "user"


class TestLogout:
    def test_logout_returns_ok(self, admin_client):
        resp = admin_client.post("/api/auth/logout")
        assert resp.status_code == 200

    def test_logout_clears_session(self, admin_client):
        admin_client.post("/api/auth/logout")
        # After logout the cookie is gone; protected routes must return 401
        resp = admin_client.get("/api/auth/me")
        assert resp.status_code == 401


class TestRunOwnership:
    def _create_run_as(self, client, username, password):
        """Login, create a run, return (run_id, client)."""
        client.post("/api/auth/login", json={"username": username, "password": password})
        resp = client.post("/api/agent/run", json={"prompt": "test run"})
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]
        client.post("/api/auth/logout")
        return run_id

    def test_user_cannot_list_other_users_runs(self, client):
        run_id = self._create_run_as(client, "alice", "Alice123!")

        client.post("/api/auth/login", json={"username": "bob", "password": "Bob123!"})
        runs = client.get("/api/agent/runs").json()
        assert not any(r["run_id"] == run_id for r in runs)

    def test_user_cannot_get_other_users_run(self, client):
        run_id = self._create_run_as(client, "alice", "Alice123!")

        client.post("/api/auth/login", json={"username": "bob", "password": "Bob123!"})
        resp = client.get(f"/api/agent/runs/{run_id}")
        assert resp.status_code == 404

    def test_admin_can_see_all_runs(self, client):
        run_id = self._create_run_as(client, "alice", "Alice123!")

        client.post("/api/auth/login", json={"username": "admin", "password": "Admin123!"})
        runs = client.get("/api/agent/runs").json()
        assert any(r["run_id"] == run_id for r in runs)

    def test_user_can_see_own_run(self, client):
        client.post("/api/auth/login", json={"username": "alice", "password": "Alice123!"})
        resp = client.post("/api/agent/run", json={"prompt": "test"})
        run_id = resp.json()["run_id"]

        runs = client.get("/api/agent/runs").json()
        assert any(r["run_id"] == run_id for r in runs)

    def test_unauthenticated_runs_list_returns_401(self, client):
        resp = client.get("/api/agent/runs")
        assert resp.status_code == 401

    def test_unauthenticated_run_post_returns_401(self, client):
        resp = client.post("/api/agent/run", json={"prompt": "test"})
        assert resp.status_code == 401


class TestUserManagement:
    def test_admin_can_list_users(self, admin_client):
        resp = admin_client.get("/api/users")
        assert resp.status_code == 200
        users = resp.json()
        assert isinstance(users, list)
        assert len(users) >= 1
        assert all("hashed_password" not in u for u in users)

    def test_non_admin_list_users_returns_403(self, user_client):
        resp = user_client.get("/api/users")
        assert resp.status_code == 403

    def test_admin_can_create_user(self, admin_client):
        resp = admin_client.post(
            "/api/users",
            json={"username": "newuser", "password": "Newpassword1!", "role": "user"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["role"] == "user"
        assert "hashed_password" not in data

    def test_non_admin_create_user_returns_403(self, user_client):
        resp = user_client.post(
            "/api/users", json={"username": "x", "password": "pw", "role": "user"}
        )
        assert resp.status_code == 403

    def test_duplicate_username_returns_409(self, admin_client):
        admin_client.post("/api/users", json={"username": "dup", "password": "pw", "role": "user"})
        resp = admin_client.post("/api/users", json={"username": "dup", "password": "pw", "role": "user"})
        assert resp.status_code == 409

    def test_admin_can_delete_user(self, admin_client):
        create_resp = admin_client.post(
            "/api/users", json={"username": "todelete", "password": "pw", "role": "user"}
        )
        user_id = create_resp.json()["user_id"]
        resp = admin_client.delete(f"/api/users/{user_id}")
        assert resp.status_code == 204

    def test_admin_cannot_delete_self(self, admin_client):
        admin_id = admin_client.get("/api/auth/me").json()["user_id"]
        resp = admin_client.delete(f"/api/users/{admin_id}")
        assert resp.status_code == 400

    def test_non_admin_delete_returns_403(self, user_client):
        resp = user_client.delete("/api/users/any-id")
        assert resp.status_code == 403

    def test_admin_can_patch_user_role(self, admin_client):
        create_resp = admin_client.post(
            "/api/users", json={"username": "topatch", "password": "pw", "role": "user"}
        )
        user_id = create_resp.json()["user_id"]
        resp = admin_client.patch(f"/api/users/{user_id}", json={"role": "admin"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    def test_user_can_patch_own_username(self, user_client):
        user_id = user_client.get("/api/auth/me").json()["user_id"]
        resp = user_client.patch(f"/api/users/{user_id}", json={"username": "alice-renamed"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "alice-renamed"

    def test_user_cannot_patch_own_role(self, user_client):
        user_id = user_client.get("/api/auth/me").json()["user_id"]
        resp = user_client.patch(f"/api/users/{user_id}", json={"role": "admin"})
        assert resp.status_code == 403

    def test_user_cannot_patch_other_user(self, client):
        # Login as admin, get admin's id, then switch to alice and try to patch admin
        client.post("/api/auth/login", json={"username": "admin", "password": "Admin123!"})
        admin_id = client.get("/api/auth/me").json()["user_id"]
        client.post("/api/auth/logout")

        client.post("/api/auth/login", json={"username": "alice", "password": "Alice123!"})
        resp = client.patch(f"/api/users/{admin_id}", json={"username": "hacked"})
        assert resp.status_code == 403


class TestChangePassword:
    def test_change_password_wrong_current_returns_400(self, user_client):
        resp = user_client.post(
            "/api/auth/change-password",
            json={"current_password": "wrong", "new_password": "NewAlice456!"},
        )
        assert resp.status_code == 400

    def test_change_password_success(self, user_client):
        resp = user_client.post(
            "/api/auth/change-password",
            json={"current_password": "Alice123!", "new_password": "NewAlice456!"},
        )
        assert resp.status_code == 200

    def test_unauthenticated_change_password_returns_401(self, client):
        resp = client.post(
            "/api/auth/change-password",
            json={"current_password": "Alice123!", "new_password": "NewAlice456!"},
        )
        assert resp.status_code == 401
