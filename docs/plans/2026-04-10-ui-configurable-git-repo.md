# UI-Configurable Remote Git Skill Repository Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow the remote Git repo URL and branch to be configured and persisted via the UI (Settings modal), replacing the `SKILLS_REPO_URL`/`SKILLS_REPO_BRANCH` env vars.

**Architecture:** A new `AppSettingsRepository` class (backed by `settings.json`) stores the repo URL and branch. The `PUT /api/settings` endpoint saves the new values, re-initialises `RemoteSkillRepository`, and immediately syncs. `GET /api/settings` returns the current values so the UI can pre-fill the form. The two env vars are removed from `config.py`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, React 18 + Vite

---

## Task 1: Create `AppSettingsRepository`

**Files:**
- Create: `app/settings_store.py`
- Create: `tests/test_settings_store.py`

**Step 1: Write the failing tests**

Create `tests/test_settings_store.py`:

```python
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.settings_store import AppSettings, AppSettingsRepository


def test_load_returns_defaults_when_file_missing(tmp_path):
    repo = AppSettingsRepository(tmp_path / "settings.json")
    s = repo.load()
    assert s.skills_repo_url is None
    assert s.skills_repo_branch == "main"


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    repo = AppSettingsRepository(path)
    repo.save(AppSettings(skills_repo_url="https://example.com/repo", skills_repo_branch="develop"))
    loaded = repo.load()
    assert loaded.skills_repo_url == "https://example.com/repo"
    assert loaded.skills_repo_branch == "develop"


def test_save_writes_valid_json(tmp_path):
    path = tmp_path / "settings.json"
    repo = AppSettingsRepository(path)
    repo.save(AppSettings(skills_repo_url="https://example.com/repo"))
    data = json.loads(path.read_text())
    assert data["skills_repo_url"] == "https://example.com/repo"


def test_load_logs_warning_on_malformed_json(tmp_path, caplog):
    path = tmp_path / "settings.json"
    path.write_text("not json {{{")
    repo = AppSettingsRepository(path)
    with caplog.at_level(logging.WARNING, logger="app.settings_store"):
        s = repo.load()
    assert s.skills_repo_url is None  # falls back to defaults
    assert "settings.json" in caplog.text or "settings" in caplog.text.lower()


def test_save_null_url(tmp_path):
    path = tmp_path / "settings.json"
    repo = AppSettingsRepository(path)
    repo.save(AppSettings(skills_repo_url=None))
    loaded = repo.load()
    assert loaded.skills_repo_url is None
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_settings_store.py -v
```
Expected: ImportError — `app.settings_store` does not exist.

**Step 3: Implement `app/settings_store.py`**

```python
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AppSettings(BaseModel):
    skills_repo_url: str | None = None
    skills_repo_branch: str = "main"


class AppSettingsRepository:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> AppSettings:
        if not self._path.exists():
            return AppSettings()
        try:
            data = json.loads(self._path.read_text())
            return AppSettings.model_validate(data)
        except Exception as exc:
            logger.warning("Could not load settings.json (%s) — using defaults", exc)
            return AppSettings()

    def save(self, s: AppSettings) -> None:
        self._path.write_text(
            json.dumps(s.model_dump(mode="json"), indent=2)
        )
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_settings_store.py -v
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add app/settings_store.py tests/test_settings_store.py
git commit -m "feat: add AppSettingsRepository for persisted app settings"
```

---

## Task 2: Add `settings_file` to `config.py` and remove `skills_repo_*` env vars

**Files:**
- Modify: `app/config.py`
- Modify: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_settings_file_default():
    s = Settings(policy_path="/tmp/policy")
    assert str(s.settings_file) == "settings.json"


def test_skills_repo_url_not_on_settings():
    s = Settings(policy_path="/tmp/policy")
    assert not hasattr(s, "skills_repo_url")
```

**Step 2: Run tests to verify the second one fails**

```bash
pytest tests/test_config.py -v
```
Expected: `test_skills_repo_url_not_on_settings` FAIL (attr still exists), `test_settings_file_default` FAIL (no such field).

**Step 3: Update `config.py`**

```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1"

    max_iterations: int = 10

    command_timeout_seconds: int = 30

    policy_path: Path

    skills_file: Path = Path("./skills.json")

    runs_file: Path = Path("./runs.json")

    settings_file: Path = Path("./settings.json")


settings = Settings()
```

Note: `Optional` import and `skills_repo_url` / `skills_repo_branch` fields are removed.

**Step 4: Run all config tests**

```bash
pytest tests/test_config.py -v
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: add settings_file to config, remove skills_repo env vars"
```

---

## Task 3: Wire `AppSettingsRepository` into `app/main.py` startup and settings endpoints

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_skills.py`

**Step 1: Write the failing tests**

Add to `tests/test_skills.py` (after the existing settings tests at the bottom):

```python
# ── AppSettings repo URL via API ──────────────────────────────────────────────

def test_api_settings_returns_skills_repo_url(tmp_path):
    from app.settings_store import AppSettings, AppSettingsRepository
    app_settings_repo = AppSettingsRepository(tmp_path / "settings.json")
    app_settings_repo.save(AppSettings(skills_repo_url="https://example.com/repo"))
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    app.state.remote_skill_repo = None
    app.state.app_settings_repo = app_settings_repo
    client = TestClient(app)
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert res.json()["skills_repo_url"] == "https://example.com/repo"
    assert res.json()["skills_repo_branch"] == "main"


def test_api_settings_returns_null_url_when_not_configured(tmp_path):
    from app.settings_store import AppSettingsRepository
    app_settings_repo = AppSettingsRepository(tmp_path / "settings.json")
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    app.state.remote_skill_repo = None
    app.state.app_settings_repo = app_settings_repo
    client = TestClient(app)
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert res.json()["skills_repo_url"] is None


def test_api_put_settings_saves_repo_url(tmp_path):
    from app.settings_store import AppSettingsRepository
    app_settings_repo = AppSettingsRepository(tmp_path / "settings.json")
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    app.state.remote_skill_repo = None
    app.state.app_settings_repo = app_settings_repo
    client = TestClient(app)
    # We mock the RemoteSkillRepository so no real git call happens
    with patch("app.main.RemoteSkillRepository") as MockRemote:
        mock_repo = MagicMock()
        MockRemote.return_value = mock_repo
        res = client.put("/api/settings", json={
            "skills_repo_url": "https://example.com/repo",
            "skills_repo_branch": "develop",
        })
    assert res.status_code == 200
    # URL is persisted
    saved = app_settings_repo.load()
    assert saved.skills_repo_url == "https://example.com/repo"
    assert saved.skills_repo_branch == "develop"
    # sync was called
    mock_repo.sync.assert_called_once()


def test_api_put_settings_clears_repo_url(tmp_path):
    from app.settings_store import AppSettings, AppSettingsRepository
    app_settings_repo = AppSettingsRepository(tmp_path / "settings.json")
    app_settings_repo.save(AppSettings(skills_repo_url="https://example.com/repo"))
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    app.state.remote_skill_repo = MagicMock()  # pretend one is configured
    app.state.app_settings_repo = app_settings_repo
    client = TestClient(app)
    res = client.put("/api/settings", json={"skills_repo_url": None})
    assert res.status_code == 200
    assert app.state.remote_skill_repo is None
    saved = app_settings_repo.load()
    assert saved.skills_repo_url is None


def test_api_put_settings_returns_503_on_sync_failure(tmp_path):
    from app.settings_store import AppSettingsRepository
    app_settings_repo = AppSettingsRepository(tmp_path / "settings.json")
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    app.state.remote_skill_repo = None
    app.state.app_settings_repo = app_settings_repo
    client = TestClient(app)
    with patch("app.main.RemoteSkillRepository") as MockRemote:
        mock_repo = MagicMock()
        mock_repo.sync.side_effect = RuntimeError("git clone failed: not found")
        MockRemote.return_value = mock_repo
        res = client.put("/api/settings", json={"skills_repo_url": "https://bad.example.com/repo"})
    assert res.status_code == 503
    assert "git clone failed" in res.json()["detail"]
    # URL is still saved despite sync failure
    saved = app_settings_repo.load()
    assert saved.skills_repo_url == "https://bad.example.com/repo"
```

**Step 2: Run the new tests to verify they fail**

```bash
pytest tests/test_skills.py::test_api_settings_returns_skills_repo_url tests/test_skills.py::test_api_put_settings_saves_repo_url -v
```
Expected: FAIL.

**Step 3: Update `app/main.py`**

3a. Add import at top:
```python
from app.settings_store import AppSettings, AppSettingsRepository
```

3b. Update `lifespan` — replace `settings.skills_repo_url` with `AppSettingsRepository`:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.skill_repo = SkillRepository(settings.skills_file)
    app_settings_repo = AppSettingsRepository(settings.settings_file)
    app.state.app_settings_repo = app_settings_repo
    app_settings = app_settings_repo.load()

    app.state.remote_skill_repo = None
    if app_settings.skills_repo_url:
        remote_repo = RemoteSkillRepository(
            app_settings.skills_repo_url,
            branch=app_settings.skills_repo_branch,
        )
        try:
            remote_repo.sync()
            app.state.remote_skill_repo = remote_repo
            logger.info("Remote skills loaded from %s", app_settings.skills_repo_url)
        except Exception as exc:
            logger.warning("Failed to load remote skills: %s", exc)

    run_repo = RunRepository(settings.runs_file)
    app.state.run_repo = run_repo
    with _runs_lock:
        _runs.update(run_repo.all_as_dict())
    yield
```

3c. Update `SettingsResponse`:
```python
class SettingsResponse(BaseModel):
    approval_required: bool
    llm_base_url: str
    has_llm_api_key: bool
    skills_repo_configured: bool
    skills_repo_url: str | None
    skills_repo_branch: str
```

3d. Update `SettingsPatchRequest` — add a sentinel for `skills_repo_url` so `None` means "clear" vs "not provided":
```python
_SETTINGS_UNSET = object()

class SettingsPatchRequest(BaseModel):
    approval_required: bool | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    skills_repo_url: str | None = None
    skills_repo_branch: str | None = None

    model_config = {"arbitrary_types_allowed": True}
```

Note: since `None` should mean "clear the URL" and absence should mean "don't touch it", use `model_fields_set` to distinguish.

3e. Update `get_settings` to return repo settings:
```python
@app.get("/api/settings", response_model=SettingsResponse)
def get_settings(request: Request) -> SettingsResponse:
    app_settings_repo = getattr(request.app.state, "app_settings_repo", None)
    app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()
    with _settings_lock:
        return SettingsResponse(
            approval_required=_approval_required,
            llm_base_url=_llm_base_url,
            has_llm_api_key=bool(_llm_api_key),
            skills_repo_configured=getattr(request.app.state, "remote_skill_repo", None) is not None,
            skills_repo_url=app_settings.skills_repo_url,
            skills_repo_branch=app_settings.skills_repo_branch,
        )
```

3f. Update `put_settings` to handle `skills_repo_url` and `skills_repo_branch`:
```python
@app.put("/api/settings", response_model=SettingsResponse)
def put_settings(body: SettingsPatchRequest, request: Request) -> SettingsResponse:
    global _approval_required, _llm_base_url, _llm_api_key

    # ── LLM settings (in-memory, unchanged) ──────────────────────────────────
    with _settings_lock:
        if body.approval_required is not None:
            _approval_required = body.approval_required
        if body.llm_base_url is not None:
            trimmed_url = body.llm_base_url.strip()
            if not trimmed_url:
                raise HTTPException(status_code=422, detail="llm_base_url must not be empty")
            _llm_base_url = trimmed_url
        if body.llm_api_key is not None:
            _llm_api_key = body.llm_api_key.strip()

    # ── Repo settings (persisted) ─────────────────────────────────────────────
    if "skills_repo_url" in body.model_fields_set:
        app_settings_repo: AppSettingsRepository = request.app.state.app_settings_repo
        app_settings = app_settings_repo.load()
        new_url = body.skills_repo_url.strip() if body.skills_repo_url else None
        new_branch = body.skills_repo_branch.strip() if body.skills_repo_branch else app_settings.skills_repo_branch
        app_settings = AppSettings(skills_repo_url=new_url, skills_repo_branch=new_branch)
        app_settings_repo.save(app_settings)

        if new_url:
            remote_repo = RemoteSkillRepository(new_url, branch=new_branch)
            try:
                remote_repo.sync()
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            request.app.state.remote_skill_repo = remote_repo
        else:
            request.app.state.remote_skill_repo = None

    app_settings_repo = getattr(request.app.state, "app_settings_repo", None)
    app_settings = app_settings_repo.load() if app_settings_repo else AppSettings()

    with _settings_lock:
        return SettingsResponse(
            approval_required=_approval_required,
            llm_base_url=_llm_base_url,
            has_llm_api_key=bool(_llm_api_key),
            skills_repo_configured=getattr(request.app.state, "remote_skill_repo", None) is not None,
            skills_repo_url=app_settings.skills_repo_url,
            skills_repo_branch=app_settings.skills_repo_branch,
        )
```

**Step 4: Run the new tests**

```bash
pytest tests/test_skills.py -v
```
Expected: all PASS.

**Step 5: Run the full suite**

```bash
pytest -v
```
Expected: all PASS except the pre-existing `test_policy.py::test_unknown_skill_id_in_overrides_is_ignored` (known pre-existing failure).

**Step 6: Commit**

```bash
git add app/main.py tests/test_skills.py
git commit -m "feat: wire AppSettingsRepository into settings API endpoints"
```

---

## Task 4: Update existing tests that check `SettingsResponse` shape

After Task 3, some existing tests in `tests/test_workflow.py` that call `GET /api/settings` or `PUT /api/settings` may fail because the response now has two extra fields (`skills_repo_url`, `skills_repo_branch`). Fix them.

**Step 1: Run workflow tests to see what breaks**

```bash
pytest tests/test_workflow.py -v -k "settings"
```

**Step 2: For each failing test**, add `app.state.app_settings_repo` setup similar to other fixtures. The pattern is:

```python
from app.settings_store import AppSettingsRepository

app.state.app_settings_repo = AppSettingsRepository(tmp_path / "settings.json")
```

Add this wherever tests set up `app.state.skill_repo` and also call `GET /api/settings` or `PUT /api/settings`.

**Step 3: Run the full suite again**

```bash
pytest -v
```
Expected: all PASS (minus the known pre-existing failure).

**Step 4: Commit**

```bash
git add tests/test_workflow.py
git commit -m "test: fix workflow tests for updated SettingsResponse shape"
```

---

## Task 5: Frontend — remote repo config section in `SkillsModal.jsx`

**Files:**
- Modify: `frontend/src/components/SkillsModal.jsx`
- Modify: `frontend/src/App.css`

**Step 1: Read the current `SkillsModal.jsx`** to locate the exact insertion point (after LLM settings section, before skill form).

**Step 2: Add state variables** for repo URL and branch (after the existing `llmToken` / `tokenHelp` state):

```javascript
const [repoUrl, setRepoUrl] = useState('')
const [repoBranch, setRepoBranch] = useState('main')
const [savingRepo, setSavingRepo] = useState(false)
const [repoSaveError, setRepoSaveError] = useState('')
```

**Step 3: Update `loadSettings`** to populate the new fields:

```javascript
// inside loadSettings(), after setLlmEndpoint:
if (typeof data.skills_repo_url === 'string') setRepoUrl(data.skills_repo_url)
else setRepoUrl('')
if (typeof data.skills_repo_branch === 'string') setRepoBranch(data.skills_repo_branch)
```

**Step 4: Add `saveRepoSettings` function** (after `saveLlmSettings`):

```javascript
async function saveRepoSettings() {
  setSavingRepo(true)
  setRepoSaveError('')
  try {
    const res = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        skills_repo_url: repoUrl.trim() || null,
        skills_repo_branch: repoBranch.trim() || 'main',
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'HTTP ' + res.status)
    }
    await loadSettings()
    await loadSkills()
  } catch (e) {
    setRepoSaveError(e.message)
  } finally {
    setSavingRepo(false)
  }
}
```

**Step 5: Add the UI section** in JSX, between the LLM settings `</div>` and the skill form block (`{skillForm !== null && ...}`):

```jsx
{/* Remote skill repository */}
<div className="settings-section">
  <div className="settings-field">
    <div className="settings-field-title">Remote skill repository URL</div>
    <input
      type="text"
      value={repoUrl}
      onChange={e => setRepoUrl(e.target.value)}
      placeholder="https://github.com/org/skills-repo"
    />
  </div>
  <div className="settings-field">
    <div className="settings-field-title">Branch</div>
    <input
      type="text"
      value={repoBranch}
      onChange={e => setRepoBranch(e.target.value)}
      placeholder="main"
    />
  </div>
  {repoSaveError && (
    <div className="settings-error">{repoSaveError}</div>
  )}
  <div className="form-actions" style={{ marginTop: 0 }}>
    <button className="btn-sm btn-secondary" onClick={saveRepoSettings} disabled={savingRepo}>
      {savingRepo ? 'Saving\u2026' : 'Save repo settings'}
    </button>
  </div>
</div>
```

**Step 6: Add `.settings-error` CSS** to `App.css`:

```css
.settings-error {
  font-size: 11px;
  color: #cf222e;
  background: #ffebe9;
  border: 1px solid #cf222e;
  border-radius: 4px;
  padding: 5px 8px;
}
```

**Step 7: Build the frontend**

```bash
npm run build
```
(Run with `workdir="frontend"`.)
Expected: build succeeds, no errors.

**Step 8: Commit**

```bash
git add frontend/src/components/SkillsModal.jsx frontend/src/App.css
git commit -m "feat: add remote repo config section to Settings modal"
```

---

## Task 6: Final verification

**Step 1: Run the full test suite**

```bash
pytest -v
```
Expected: all PASS except the known pre-existing `test_policy.py::test_unknown_skill_id_in_overrides_is_ignored`.

**Step 2: Update `.env.example`** — comment out (or remove) the `SKILLS_REPO_*` lines since they are no longer read:

```
# Note: SKILLS_REPO_URL and SKILLS_REPO_BRANCH are now configured via the UI
# (Settings modal → Remote skill repository). The env vars below are no longer used.
# SKILLS_REPO_URL=https://github.com/org/skills-repo
# SKILLS_REPO_BRANCH=main
```

**Step 3: Update `README.md`** — change the configuration table to reflect that `SKILLS_REPO_URL`/`SKILLS_REPO_BRANCH` are gone, and update the "Remote skill repository" section to say it's configured via the UI.

**Step 4: Commit any remaining changes**

```bash
git add .env.example README.md
git commit -m "docs: update config docs to reflect UI-managed repo settings"
```

---

## Summary of files changed

| File | Change |
|---|---|
| `app/settings_store.py` | New — `AppSettings` + `AppSettingsRepository` |
| `app/config.py` | Add `settings_file`; remove `skills_repo_url`, `skills_repo_branch` |
| `app/main.py` | Load `AppSettingsRepository` in lifespan; update `SettingsResponse`, `SettingsPatchRequest`, `get_settings`, `put_settings` |
| `tests/test_settings_store.py` | New — unit tests for `AppSettingsRepository` |
| `tests/test_config.py` | Add `settings_file` test; add `skills_repo_url` removal test |
| `tests/test_skills.py` | Add API tests for repo URL save/clear/error |
| `tests/test_workflow.py` | Fix tests that assert on `SettingsResponse` shape |
| `frontend/src/components/SkillsModal.jsx` | Add repo config section with URL + branch inputs |
| `frontend/src/App.css` | Add `.settings-error` style |
| `.env.example` | Note that env vars are replaced by UI config |
| `README.md` | Update remote repo section and config table |
