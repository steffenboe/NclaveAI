import os
import tempfile

_tmpdir = tempfile.mkdtemp(prefix="pytest_llm_agent_")


def pytest_configure(config):
    """Set required env vars before any module is imported."""
    os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "Admin123!")
    os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")
    # Redirect only the new users file so the lifespan bootstrap never writes
    # to the project root.  The client fixture replaces all other repos anyway.
    os.environ.setdefault("USERS_FILE", f"{_tmpdir}/users.json")


import pytest  # noqa: E402  (must come after pytest_configure has run)


@pytest.fixture(autouse=True)
def _bypass_auth_for_non_auth_tests(request):
    """Install a fake admin user for all tests that don't specifically test auth.

    Tests in test_main_auth.py and test_main_rbac.py opt out of this bypass so
    they exercise the real login / cookie / JWT / RBAC flow.
    """
    _real_auth_files = ("test_main_auth", "test_main_rbac", "test_scheduled_tasks_api")
    if any(name in request.node.nodeid for name in _real_auth_files):
        yield
        return

    from datetime import datetime, timezone

    from app.auth import get_current_user
    from app.main import app
    from app.models import User

    fake_admin = User(
        user_id="test-admin-id",
        username="admin",
        hashed_password="",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )
    app.dependency_overrides[get_current_user] = lambda: fake_admin
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client(tmp_path):
    """Function-scoped TestClient with fully isolated tmp_path-backed repositories."""
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.auth import hash_password
    from app.main import app
    from app.runs import RunRepository
    from app.scheduled_tasks import ScheduledTaskRepository
    from app.secrets_store import SecretsStore
    from app.settings_store import AppSettingsRepository
    from app.skills import SkillRepository
    from app.users import UserRepository

    # Pre-populate a fresh user repo with known test users
    user_repo = UserRepository(tmp_path / "users.json")
    user_repo.create(username="admin", hashed_password=hash_password("Admin123!"), role="admin")
    user_repo.create(username="alice", hashed_password=hash_password("Alice123!"), role="user")
    user_repo.create(username="bob", hashed_password=hash_password("Bob123!"), role="user")

    with TestClient(app, raise_server_exceptions=True) as c:
        # Clear module-level in-memory state left over from lifespan / previous tests
        with main_module._runs_lock:
            main_module._runs.clear()
        with main_module._scheduled_tasks_lock:
            main_module._scheduled_tasks.clear()

        # Replace all app.state repos with fresh, isolated tmp_path instances
        app.state.run_repo = RunRepository(tmp_path / "runs.json")
        app.state.scheduled_task_repo = ScheduledTaskRepository(tmp_path / "scheduled_tasks.json")
        app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
        app.state.app_settings_repo = AppSettingsRepository(tmp_path / "settings.json")
        app.state.secrets_store = SecretsStore(tmp_path / "secrets.json")
        app.state.user_repo = user_repo
        app.state.remote_skill_repo = None

        yield c


@pytest.fixture
def admin_client(client):
    """TestClient with an active admin session cookie."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123!"})
    assert resp.status_code == 200
    return client


@pytest.fixture
def user_client(client):
    """TestClient with an active alice (regular user) session cookie."""
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "Alice123!"})
    assert resp.status_code == 200
    return client
