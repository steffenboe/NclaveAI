from __future__ import annotations


class TestSettingsRBAC:
    def test_unauthenticated_get_settings_returns_401(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 401

    def test_non_admin_get_settings_returns_403(self, user_client):
        resp = user_client.get("/api/settings")
        assert resp.status_code == 403

    def test_non_admin_put_settings_returns_403(self, user_client):
        resp = user_client.put("/api/settings", json={"approval_required": True})
        assert resp.status_code == 403

    def test_admin_can_get_settings(self, admin_client):
        resp = admin_client.get("/api/settings")
        assert resp.status_code == 200

    def test_admin_can_put_settings(self, admin_client):
        resp = admin_client.put("/api/settings", json={"approval_required": False})
        assert resp.status_code == 200


class TestSkillsRBAC:
    def test_unauthenticated_list_skills_returns_401(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 401

    def test_regular_user_can_list_skills(self, user_client):
        resp = user_client.get("/api/skills")
        assert resp.status_code == 200

    def test_non_admin_create_skill_returns_403(self, user_client):
        resp = user_client.post("/api/skills", json={"name": "x", "description": "y"})
        assert resp.status_code == 403

    def test_non_admin_patch_skill_returns_403(self, user_client):
        # Auth check fires before skill lookup — no real skill needed
        resp = user_client.patch("/api/skills/any-id", json={"name": "x"})
        assert resp.status_code == 403

    def test_non_admin_delete_skill_returns_403(self, user_client):
        resp = user_client.delete("/api/skills/any-id")
        assert resp.status_code == 403

    def test_non_admin_sync_returns_403(self, user_client):
        resp = user_client.post("/api/skills/sync")
        assert resp.status_code == 403

    def test_non_admin_generate_policy_returns_403(self, user_client):
        resp = user_client.post(
            "/api/skills/generate-policy",
            json={"skill_name": "x", "skill_description": "y", "description": "z"},
        )
        assert resp.status_code == 403

    def test_admin_can_create_skill(self, admin_client):
        resp = admin_client.post(
            "/api/skills", json={"name": "admin-skill", "description": "desc"}
        )
        assert resp.status_code == 201

    def test_admin_can_delete_skill(self, admin_client):
        create_resp = admin_client.post(
            "/api/skills", json={"name": "to-delete", "description": "desc"}
        )
        skill_id = create_resp.json()["id"]
        resp = admin_client.delete(f"/api/skills/{skill_id}")
        assert resp.status_code == 204


class TestModelsRBAC:
    def test_unauthenticated_get_models_returns_401(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 401

    def test_regular_user_can_request_models(self, user_client):
        # Auth passes; may 502 if no LLM endpoint is reachable — that's fine
        resp = user_client.get("/api/models")
        assert resp.status_code != 401
        assert resp.status_code != 403
