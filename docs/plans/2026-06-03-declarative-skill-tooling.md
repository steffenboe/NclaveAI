# Declarative Skill Tooling

**Goal:** An admin can declare which system packages a skill requires. The agent installs them on first use and re-installs them on container restart, so that no manual Dockerfile changes or image rebuilds are needed when adding a new skill.

**User Story:**
> As an admin, I want to declare the system packages a skill needs so that the required CLI is automatically available in the container — without rebuilding the Docker image.

**Acceptance Criteria:**
- A skill can optionally declare one or more apt package names via a new `packages` field.
- When the agent starts, all packages declared by existing skills are installed idempotently.
- When a skill is created or updated via the API, its packages are installed immediately.
- The installation result (packages installed, already-present, or failed) is logged.
- If a package installation fails, the skill is saved but marked with a warning; the agent does not crash.
- Skills without a `packages` field behave exactly as before (no regression).
- The `packages` field is persisted in `skills.json` and (when MongoDB is used) in the DB.

**Design Decisions:**
- `packages` is a `list[str]` of apt package names, e.g. `["kubectl"]`. The agent calls `apt-get install -y --no-install-recommends` which is idempotent.
- The container must run as root (or have sudo) for apt-get — the current Dockerfile already does this (no `USER` directive).
- Source of truth is the skill definition, not the container filesystem. On every startup, the agent re-ensures all packages, making the runtime reproducible as long as the apt repos are available.
- No volume mount is needed: apt installs into the normal container filesystem. On container restart this filesystem is reset to the image state, so re-installation happens automatically.
- Installation is performed in a background thread at startup (non-blocking) and inline (blocking, fast-fail) on skill create/update so the admin gets immediate feedback.

**Known Limitations:**
- Package availability depends on the apt sources in the image. Packages not in the default Debian/Ubuntu repos require manual source additions (out of scope).
- This is not reproducible in the strict immutable-infrastructure sense: the running container diverges from its image. This is an accepted trade-off for operational simplicity.
- Not suitable for Kubernetes deployments where containers are ephemeral and may be rescheduled frequently (re-installation overhead on every pod start).

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, `subprocess` (stdlib), `apt-get` (Debian-based image)

---

## Task 1: Extend the `Skill` model with a `packages` field

**Files:**
- Modify: `app/skills.py`
- Test: `tests/test_skills.py`

**Step 1: Write the failing tests**

Add to `tests/test_skills.py`:

```python
def test_skill_packages_defaults_to_empty():
    from app.skills import Skill
    from datetime import datetime, timezone
    s = Skill(id="x", name="n", description="d", created_at=datetime.now(timezone.utc))
    assert s.packages == []


def test_skill_packages_persisted(tmp_path):
    from app.skills import SkillRepository
    repo = SkillRepository(tmp_path / "skills.json")
    skill = repo.create(name="kubectl", description="k8s", packages=["kubectl"])
    assert skill.packages == ["kubectl"]
    # reload
    repo2 = SkillRepository(tmp_path / "skills.json")
    assert repo2.get(skill.id).packages == ["kubectl"]
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_skills.py::test_skill_packages_defaults_to_empty tests/test_skills.py::test_skill_packages_persisted -v
```

**Step 3: Add `packages` field to `Skill`**

In `app/skills.py`:

```python
packages: list[str] = []    # apt package names to install at startup / skill create
```

**Step 4: Add `packages` parameter to `SkillRepository.create` and `update`**

`create`:
```python
def create(self, name: str, description: str, enabled: bool = True,
           policy: str | None = None, env: list[str] | None = None,
           packages: list[str] | None = None) -> Skill:
    skill = Skill(
        ...
        packages=packages or [],
    )
```

`update` — add `packages` as an optional keyword argument, same pattern as `env`.

**Step 5: Run tests and the full suite**

```bash
pytest tests/test_skills.py -v
pytest -x -q
```

---

## Task 2: Implement `ToolInstaller`

**Files:**
- New: `app/tool_installer.py`
- Test: `tests/test_tool_installer.py`

**Step 1: Write the failing tests**

```python
# tests/test_tool_installer.py
from unittest.mock import patch, MagicMock
from app.tool_installer import ensure_packages


def test_ensure_packages_empty_list_is_noop():
    with patch("subprocess.run") as mock_run:
        ensure_packages([])
        mock_run.assert_not_called()


def test_ensure_packages_calls_apt(monkeypatch):
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=completed) as mock_run:
        ensure_packages(["curl", "jq"])
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "apt-get" in args
        assert "curl" in args
        assert "jq" in args


def test_ensure_packages_raises_on_failure():
    from app.tool_installer import PackageInstallError
    completed = MagicMock(returncode=1, stdout="", stderr="E: Unable to locate package foo")
    with patch("subprocess.run", return_value=completed):
        with pytest.raises(PackageInstallError):
            ensure_packages(["foo"])
```

**Step 2: Implement `app/tool_installer.py`**

```python
from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


class PackageInstallError(RuntimeError):
    pass


def ensure_packages(packages: list[str], *, timeout: int = 120) -> None:
    """Install apt packages idempotently. No-op if packages is empty."""
    if not packages:
        return
    cmd = [
        "apt-get", "install", "-y", "--no-install-recommends",
        *packages,
    ]
    logger.info("Installing packages: %s", packages)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise PackageInstallError(
            f"apt-get failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    logger.info("Packages installed successfully: %s", packages)
```

**Step 3: Run tests**

```bash
pytest tests/test_tool_installer.py -v
```

---

## Task 3: Install packages at startup

**Files:**
- Modify: `app/main.py`

On application startup (in the `lifespan` context manager), after skills are loaded, iterate over all skills and call `ensure_packages` for any skill that has packages declared. Run this in a background thread (via `asyncio.to_thread`) so it does not block the event loop.

Log a warning (do not crash) if installation fails for any individual skill.

```python
# inside lifespan, after skill_repo is initialised
async def _install_all_skill_packages() -> None:
    from app.tool_installer import ensure_packages, PackageInstallError
    for skill in skill_repo.list():
        if skill.packages:
            try:
                await asyncio.to_thread(ensure_packages, skill.packages)
            except PackageInstallError as exc:
                logger.warning("Failed to install packages for skill %r: %s", skill.name, exc)

asyncio.create_task(_install_all_skill_packages())
```

**Test:** Add an integration test in `tests/test_main_rbac.py` or a new `tests/test_main_tooling.py` that patches `ensure_packages` and verifies it is called for a skill with packages on startup.

---

## Task 4: Install packages on skill create/update via API

**Files:**
- Modify: `app/main.py` (POST `/api/skills` and PATCH `/api/skills/{id}` handlers)

After persisting the skill, if `packages` is non-empty, call `ensure_packages` synchronously (blocking, inside `await asyncio.to_thread`). Return a `400` with a clear error message if installation fails, so the admin sees immediate feedback.

```python
from app.tool_installer import ensure_packages, PackageInstallError

# in POST /api/skills:
if skill.packages:
    try:
        await asyncio.to_thread(ensure_packages, skill.packages)
    except PackageInstallError as exc:
        skill_repo.delete(skill.id)
        raise HTTPException(status_code=400, detail=f"Package installation failed: {exc}")
```

For PATCH, only re-run `ensure_packages` when the `packages` field is actually changed in the request body.

**Tests:**

```python
def test_create_skill_with_packages_calls_installer(client, admin_token):
    with patch("app.main.ensure_packages") as mock_install:
        resp = client.post("/api/skills", json={
            "name": "kubectl", "description": "k8s cli", "packages": ["kubectl"]
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 201
        mock_install.assert_called_once_with(["kubectl"])


def test_create_skill_package_install_failure_returns_400(client, admin_token):
    from app.tool_installer import PackageInstallError
    with patch("app.main.ensure_packages", side_effect=PackageInstallError("not found")):
        resp = client.post("/api/skills", json={
            "name": "bad", "description": "bad", "packages": ["nonexistent-pkg"]
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 400
        assert "Package installation failed" in resp.json()["detail"]
```

---

## Task 5: Expose `packages` in the API schema and UI

**Files:**
- Modify: `app/main.py` (request/response models for skills)
- Modify: `frontend/src/` (skill create/edit form)

**Backend:** Ensure the `SkillCreate` / `SkillUpdate` Pydantic models include `packages: list[str] = []`. The field is already present on `Skill`, so the response model picks it up automatically.

**Frontend:** Add a text input to the skill create/edit form for packages (comma-separated or one-per-line). Display the current `packages` list in the skill detail view.

No specific implementation steps here — adjust to the existing form component pattern in the frontend.
