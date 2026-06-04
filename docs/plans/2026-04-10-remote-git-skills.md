# Remote Git Skill Repository Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow skills to be loaded from a remote public Git repository (cloned at startup, manually re-synced via API) as a read-only overlay on top of local skills.

**Architecture:** A new `RemoteSkillRepository` class shells out to `git clone/pull` to maintain a local cache of the remote repo. It parses top-level `*.yaml` files into `Skill` objects with deterministic IDs. At startup, when `SKILLS_REPO_URL` is configured, the remote skills are loaded and merged with local skills across all API endpoints. A `POST /api/skills/sync` endpoint triggers a manual re-sync.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, PyYAML (already a transitive dependency via langchain), subprocess (stdlib), React 18 + Vite

---

## Task 1: Extend the `Skill` model with a `source` field

**Files:**
- Modify: `app/skills.py`
- Test: `tests/test_skills.py`

**Step 1: Write the failing test**

Add this test to `tests/test_skills.py`:

```python
def test_skill_default_source_is_local():
    from app.skills import Skill
    from datetime import datetime, timezone
    s = Skill(id="x", name="n", description="d", created_at=datetime.now(timezone.utc))
    assert s.source == "local"


def test_skill_source_remote():
    from app.skills import Skill
    from datetime import datetime, timezone
    s = Skill(id="x", name="n", description="d", created_at=datetime.now(timezone.utc), source="remote")
    assert s.source == "remote"
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_skills.py::test_skill_default_source_is_local -v
```
Expected: FAIL — `Skill` has no `source` field.

**Step 3: Add `source` field to `Skill`**

In `app/skills.py`, add one field to the `Skill` model:

```python
source: str = "local"   # "local" | "remote" — not persisted in skills.json
```

Also update `_save()` in `SkillRepository` to exclude `source` when serializing, so it is never written to `skills.json`:

```python
def _save(self) -> None:
    self._path.write_text(
        json.dumps(
            [s.model_dump(mode="json", exclude={"source"}) for s in self._skills],
            indent=2,
        )
    )
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_skills.py -v
```
Expected: all PASS (including existing tests — the new field has a default).

**Step 5: Commit**

```bash
git add app/skills.py tests/test_skills.py
git commit -m "feat: add source field to Skill model (local/remote, not persisted)"
```

---

## Task 2: Add `SKILLS_REPO_URL` and `SKILLS_REPO_BRANCH` to `Settings`

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_skills_repo_url_defaults_to_none(monkeypatch):
    monkeypatch.delenv("SKILLS_REPO_URL", raising=False)
    monkeypatch.delenv("SKILLS_REPO_BRANCH", raising=False)
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    assert cfg.settings.skills_repo_url is None


def test_skills_repo_branch_defaults_to_main(monkeypatch):
    monkeypatch.delenv("SKILLS_REPO_URL", raising=False)
    monkeypatch.delenv("SKILLS_REPO_BRANCH", raising=False)
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    assert cfg.settings.skills_repo_branch == "main"
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_config.py -v
```
Expected: FAIL — `settings` has no `skills_repo_url` attribute.

**Step 3: Add the fields to `Settings`**

In `app/config.py`:

```python
from typing import Optional

class Settings(BaseSettings):
    # ... existing fields ...
    skills_repo_url: Optional[str] = None
    skills_repo_branch: str = "main"
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```
Expected: all PASS.

**Step 5: Update `.env.example`**

Add these lines to `.env.example` (create it if it does not exist):

```
# Optional: load skills from a remote public Git repository
# SKILLS_REPO_URL=https://github.com/org/skills-repo
# SKILLS_REPO_BRANCH=main
```

**Step 6: Commit**

```bash
git add app/config.py tests/test_config.py .env.example
git commit -m "feat: add SKILLS_REPO_URL and SKILLS_REPO_BRANCH config settings"
```

---

## Task 3: Implement `RemoteSkillRepository`

**Files:**
- Modify: `app/skills.py`
- Create: `tests/test_remote_skills.py`

**Step 1: Write the failing tests**

Create `tests/test_remote_skills.py`:

```python
from __future__ import annotations

import uuid
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from app.skills import RemoteSkillRepository, Skill


# ── YAML parsing ──────────────────────────────────────────────────────────────

def test_parse_full_yaml_file(tmp_path):
    (tmp_path / "kubectl.yaml").write_text(
        "name: kubectl\ndescription: Kubernetes CLI\nenabled: true\npolicy: |\n  allow { true }\n"
    )
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    skills = repo._parse_yaml_files()
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "kubectl"
    assert s.description == "Kubernetes CLI"
    assert s.enabled is True
    assert "allow { true }" in s.policy
    assert s.source == "remote"


def test_parse_minimal_yaml_file(tmp_path):
    (tmp_path / "simple.yaml").write_text("name: simple\ndescription: A simple skill\n")
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    skills = repo._parse_yaml_files()
    assert len(skills) == 1
    assert skills[0].enabled is True
    assert skills[0].policy is None


def test_parse_yaml_file_skips_subdirectory(tmp_path):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.yaml").write_text("name: nested\ndescription: Should be ignored\n")
    (tmp_path / "top.yaml").write_text("name: top\ndescription: Top level\n")
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    skills = repo._parse_yaml_files()
    assert len(skills) == 1
    assert skills[0].name == "top"


def test_parse_yaml_file_skips_malformed(tmp_path, caplog):
    import logging
    (tmp_path / "bad.yaml").write_text(": invalid: yaml: {{{{")
    (tmp_path / "good.yaml").write_text("name: good\ndescription: Fine\n")
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    with caplog.at_level(logging.WARNING, logger="app.skills"):
        skills = repo._parse_yaml_files()
    assert len(skills) == 1
    assert skills[0].name == "good"


def test_parse_yaml_file_skips_missing_required_fields(tmp_path, caplog):
    import logging
    (tmp_path / "noname.yaml").write_text("description: Missing name field\n")
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = "https://example.com/repo"
    repo._branch = "main"
    repo._skills = []
    with caplog.at_level(logging.WARNING, logger="app.skills"):
        skills = repo._parse_yaml_files()
    assert len(skills) == 0


def test_skill_ids_are_deterministic(tmp_path):
    (tmp_path / "kubectl.yaml").write_text("name: kubectl\ndescription: k8s CLI\n")
    repo_url = "https://example.com/repo"
    repo = RemoteSkillRepository.__new__(RemoteSkillRepository)
    repo._cache_dir = tmp_path
    repo._repo_url = repo_url
    repo._branch = "main"
    repo._skills = []
    skills1 = repo._parse_yaml_files()
    skills2 = repo._parse_yaml_files()
    assert skills1[0].id == skills2[0].id


# ── sync via subprocess ───────────────────────────────────────────────────────

def test_sync_clones_when_cache_empty(tmp_path):
    cache_dir = tmp_path / "cache"
    repo = RemoteSkillRepository("https://example.com/repo", branch="main", cache_dir=cache_dir)
    with patch("app.skills.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        # cache_dir has no .git → clone path
        repo.sync()
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "clone" in call_args


def test_sync_pulls_when_cache_exists(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ".git").mkdir()
    repo = RemoteSkillRepository("https://example.com/repo", branch="main", cache_dir=cache_dir)
    with patch("app.skills.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        repo.sync()
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "pull" in call_args


def test_sync_raises_on_git_failure(tmp_path):
    cache_dir = tmp_path / "cache"
    repo = RemoteSkillRepository("https://example.com/repo", branch="main", cache_dir=cache_dir)
    with patch("app.skills.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="fatal: repo not found")
        with pytest.raises(RuntimeError, match="git"):
            repo.sync()


def test_list_skills_returns_last_synced(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / ".git").mkdir()
    (cache_dir / "tool.yaml").write_text("name: tool\ndescription: A tool\n")
    repo = RemoteSkillRepository("https://example.com/repo", branch="main", cache_dir=cache_dir)
    with patch("app.skills.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        repo.sync()
    skills = repo.list_skills()
    assert len(skills) == 1
    assert skills[0].name == "tool"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_remote_skills.py -v
```
Expected: FAIL — `RemoteSkillRepository` not imported.

**Step 3: Implement `RemoteSkillRepository`**

Add the following to `app/skills.py` (after the existing imports, add `import subprocess` and `import yaml`; then add the class after `SkillRepository`):

```python
import subprocess
import yaml
import hashlib


class RemoteSkillRepository:
    def __init__(
        self,
        repo_url: str,
        branch: str = "main",
        cache_dir: Path | None = None,
    ) -> None:
        self._repo_url = repo_url
        self._branch = branch
        if cache_dir is None:
            url_hash = hashlib.md5(repo_url.encode()).hexdigest()[:12]
            cache_dir = Path("/tmp") / f"NclaveOS-skills-{url_hash}"
        self._cache_dir = Path(cache_dir)
        self._skills: list[Skill] = []

    def sync(self) -> list[Skill]:
        if (self._cache_dir / ".git").exists():
            cmd = ["git", "-C", str(self._cache_dir), "pull"]
        else:
            cmd = [
                "git", "clone",
                "--depth", "1",
                "--branch", self._branch,
                self._repo_url,
                str(self._cache_dir),
            ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"git command failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        self._skills = self._parse_yaml_files()
        return list(self._skills)

    def list_skills(self) -> list[Skill]:
        return list(self._skills)

    def _parse_yaml_files(self) -> list[Skill]:
        skills: list[Skill] = []
        for yaml_file in sorted(self._cache_dir.glob("*.yaml")):
            if not yaml_file.is_file():
                continue
            try:
                data = yaml.safe_load(yaml_file.read_text())
                if not isinstance(data, dict):
                    raise ValueError("YAML root must be a mapping")
                name = data.get("name")
                description = data.get("description")
                if not name or not description:
                    raise ValueError("'name' and 'description' are required")
                skill_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{self._repo_url}#{yaml_file.name}",
                    )
                )
                skills.append(
                    Skill(
                        id=skill_id,
                        name=str(name),
                        description=str(description),
                        enabled=bool(data.get("enabled", True)),
                        policy=data.get("policy") or None,
                        created_at=datetime.now(timezone.utc),
                        source="remote",
                    )
                )
            except Exception as exc:
                logger.warning("Skipping %s: %s", yaml_file.name, exc)
        return skills
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_remote_skills.py -v
```
Expected: all PASS.

**Step 5: Also run existing skills tests**

```bash
pytest tests/test_skills.py -v
```
Expected: all PASS (`_save()` still works because `source` defaults to `"local"` and is excluded from serialization).

**Step 6: Commit**

```bash
git add app/skills.py tests/test_remote_skills.py
git commit -m "feat: implement RemoteSkillRepository with git clone/pull and YAML parsing"
```

---

## Task 4: Wire `RemoteSkillRepository` into the FastAPI app

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_skills.py` (add API integration tests)

**Step 1: Write the failing tests**

Add to `tests/test_skills.py`:

```python
# ── Remote skills overlay ─────────────────────────────────────────────────────

from unittest.mock import MagicMock


@pytest.fixture
def client_with_remote(tmp_path):
    from app.skills import RemoteSkillRepository
    from datetime import datetime, timezone

    local_repo = SkillRepository(tmp_path / "skills.json")
    local_repo.create(name="local-tool", description="A local skill")

    remote_repo = MagicMock(spec=RemoteSkillRepository)
    remote_skill = Skill(
        id="remote-id-1",
        name="remote-tool",
        description="A remote skill",
        enabled=True,
        policy=None,
        created_at=datetime.now(timezone.utc),
        source="remote",
    )
    remote_repo.list_skills.return_value = [remote_skill]

    app.state.skill_repo = local_repo
    app.state.remote_skill_repo = remote_repo
    yield TestClient(app)
    # cleanup
    app.state.remote_skill_repo = None


def test_api_list_includes_remote_skills(client_with_remote):
    res = client_with_remote.get("/api/skills")
    assert res.status_code == 200
    names = [s["name"] for s in res.json()]
    assert "remote-tool" in names
    assert "local-tool" in names


def test_api_list_remote_skill_has_source_field(client_with_remote):
    res = client_with_remote.get("/api/skills")
    remote = next(s for s in res.json() if s["name"] == "remote-tool")
    assert remote["source"] == "remote"


def test_api_delete_remote_skill_returns_404(client_with_remote):
    res = client_with_remote.delete("/api/skills/remote-id-1")
    assert res.status_code == 404


def test_api_patch_remote_skill_returns_404(client_with_remote):
    res = client_with_remote.patch("/api/skills/remote-id-1", json={"name": "x"})
    assert res.status_code == 404


def test_api_sync_returns_combined_skills(client_with_remote):
    res = client_with_remote.post("/api/skills/sync")
    assert res.status_code == 200
    names = [s["name"] for s in res.json()["skills"]]
    assert "remote-tool" in names


def test_api_sync_returns_404_when_no_remote_repo(tmp_path):
    local_repo = SkillRepository(tmp_path / "skills.json")
    app.state.skill_repo = local_repo
    app.state.remote_skill_repo = None
    client = TestClient(app)
    res = client.post("/api/skills/sync")
    assert res.status_code == 404


def test_api_settings_includes_skills_repo_configured_false(tmp_path):
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    app.state.remote_skill_repo = None
    client = TestClient(app)
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert res.json()["skills_repo_configured"] is False


def test_api_settings_includes_skills_repo_configured_true(client_with_remote):
    res = client_with_remote.get("/api/settings")
    assert res.status_code == 200
    assert res.json()["skills_repo_configured"] is True
```

**Step 2: Run the new tests to verify they fail**

```bash
pytest tests/test_skills.py::test_api_list_includes_remote_skills tests/test_skills.py::test_api_sync_returns_404_when_no_remote_repo -v
```
Expected: FAIL.

**Step 3: Update `app/main.py`**

3a. Update imports:
```python
from app.skills import RemoteSkillRepository, SkillRepository
```

3b. Update `lifespan` to also initialize the remote repo:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.skill_repo = SkillRepository(settings.skills_file)
    app.state.remote_skill_repo = None
    if settings.skills_repo_url:
        remote_repo = RemoteSkillRepository(
            settings.skills_repo_url,
            branch=settings.skills_repo_branch,
        )
        try:
            remote_repo.sync()
            app.state.remote_skill_repo = remote_repo
            logger.info("Remote skills loaded from %s", settings.skills_repo_url)
        except Exception as exc:
            logger.warning("Failed to load remote skills: %s", exc)
    run_repo = RunRepository(settings.runs_file)
    app.state.run_repo = run_repo
    with _runs_lock:
        _runs.update(run_repo.all_as_dict())
    yield
```

3c. Add a helper function `_all_skills(request)` that merges remote + local:
```python
def _all_skills(request: Request) -> list:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    remote = remote_repo.list_skills() if remote_repo else []
    local = request.app.state.skill_repo.list()
    return remote + local
```

3d. Update `list_skills` endpoint to use `_all_skills`:
```python
@app.get("/api/skills")
def list_skills(request: Request) -> list:
    return [s.model_dump(mode="json") for s in _all_skills(request)]
```

3e. Update `delete_skill` and `patch_skill` to reject remote skill IDs:
```python
@app.patch("/api/skills/{skill_id}")
def patch_skill(skill_id: str, body: SkillPatchRequest, request: Request) -> Any:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo:
        remote_ids = {s.id for s in remote_repo.list_skills()}
        if skill_id in remote_ids:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} is read-only (remote)")
    # ... existing logic ...

@app.delete("/api/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, request: Request) -> None:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo:
        remote_ids = {s.id for s in remote_repo.list_skills()}
        if skill_id in remote_ids:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} is read-only (remote)")
    # ... existing logic ...
```

3f. Add the `POST /api/skills/sync` endpoint (place it before the existing skill endpoints):
```python
@app.post("/api/skills/sync")
def sync_remote_skills(request: Request) -> dict:
    remote_repo = getattr(request.app.state, "remote_skill_repo", None)
    if remote_repo is None:
        raise HTTPException(status_code=404, detail="No remote skill repository configured")
    try:
        remote_repo.sync()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"skills": [s.model_dump(mode="json") for s in _all_skills(request)]}
```

3g. Update `SettingsResponse` and `get_settings` to expose `skills_repo_configured`:
```python
class SettingsResponse(BaseModel):
    approval_required: bool
    llm_base_url: str
    has_llm_api_key: bool
    skills_repo_configured: bool

@app.get("/api/settings", response_model=SettingsResponse)
def get_settings(request: Request) -> SettingsResponse:
    with _settings_lock:
        return SettingsResponse(
            approval_required=_approval_required,
            llm_base_url=_llm_base_url,
            has_llm_api_key=bool(_llm_api_key),
            skills_repo_configured=getattr(request.app.state, "remote_skill_repo", None) is not None,
        )
```

Note: `get_settings` now needs a `request: Request` parameter.

Also update `put_settings` to include `skills_repo_configured` in its response:
```python
@app.put("/api/settings", response_model=SettingsResponse)
def put_settings(body: SettingsPatchRequest, request: Request) -> SettingsResponse:
    global _approval_required, _llm_base_url, _llm_api_key
    with _settings_lock:
        # ... existing logic ...
        return SettingsResponse(
            approval_required=_approval_required,
            llm_base_url=_llm_base_url,
            has_llm_api_key=bool(_llm_api_key),
            skills_repo_configured=getattr(request.app.state, "remote_skill_repo", None) is not None,
        )
```

3h. Update `_build_workflow` to pass the combined skills list to `PolicyEvaluator`:
```python
def _build_workflow(
    skill_repo: SkillRepository,
    run_id: str | None = None,
    ctx: RunContext | None = None,
    remote_skill_repo=None,
) -> AgentWorkflow:
    local_skills = skill_repo.list()
    remote_skills = remote_skill_repo.list_skills() if remote_skill_repo else []
    all_skills = remote_skills + local_skills
    # ... rest unchanged, but use all_skills for PolicyEvaluator ...
    return AgentWorkflow(
        planner=Planner(
            skill_repo,
            remote_skills=remote_skills,
            # ... existing args ...
        ),
        policy=PolicyEvaluator(skills=all_skills),
        # ...
    )
```

Note: `Planner._build_system_prompt()` currently calls `self._skill_repo.list()`. We need to also pass remote skills to it (see Task 5).

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_skills.py -v
```
Expected: all PASS.

**Step 5: Run the full test suite**

```bash
pytest -v
```
Expected: all PASS.

**Step 6: Commit**

```bash
git add app/main.py tests/test_skills.py
git commit -m "feat: wire RemoteSkillRepository into FastAPI app with sync endpoint and settings flag"
```

---

## Task 5: Pass remote skills to `Planner`

**Files:**
- Modify: `app/planner.py`
- Modify: `app/main.py`
- Test: `tests/test_planner.py`

**Step 1: Read `tests/test_planner.py` to understand existing test patterns**

Read the file before writing tests to see how `Planner` is currently tested (e.g., `Planner.__new__` bypass).

**Step 2: Write the failing test**

Add to `tests/test_planner.py`:

```python
def test_system_prompt_includes_remote_skill():
    from app.skills import Skill, SkillRepository
    from app.planner import Planner
    from datetime import datetime, timezone

    planner = Planner.__new__(Planner)
    planner._skill_repo = MagicMock()
    planner._skill_repo.list.return_value = []
    planner._remote_skills = [
        Skill(
            id="r1",
            name="remote-tool",
            description="A remote tool description",
            enabled=True,
            created_at=datetime.now(timezone.utc),
            source="remote",
        )
    ]
    prompt = planner._build_system_prompt()
    assert "remote-tool" in prompt
    assert "A remote tool description" in prompt
```

**Step 3: Run test to verify it fails**

```bash
pytest tests/test_planner.py -v -k "test_system_prompt_includes_remote_skill"
```
Expected: FAIL.

**Step 4: Update `Planner`**

In `app/planner.py`:

- Add `remote_skills: list | None = None` parameter to `__init__`:
```python
def __init__(
    self,
    skill_repo: SkillRepository,
    remote_skills: list | None = None,
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
    llm_model: str | None = None,
) -> None:
    self._skill_repo = skill_repo
    self._remote_skills: list = remote_skills or []
    # ... rest unchanged ...
```

- Update `_build_system_prompt` to include both local and remote enabled skills:
```python
def _build_system_prompt(self) -> str:
    local_enabled = [s for s in self._skill_repo.list() if s.enabled]
    remote_enabled = [s for s in self._remote_skills if s.enabled]
    enabled = remote_enabled + local_enabled
    # ... rest of existing logic uses `enabled` unchanged ...
```

**Step 5: Update `_build_workflow` in `app/main.py`** to pass `remote_skills` to `Planner`:
```python
planner=Planner(
    skill_repo,
    remote_skills=remote_skills,
    llm_base_url=llm_base_url,
    llm_api_key=llm_api_key,
    llm_model=settings.llm_model,
),
```

**Step 6: Run tests to verify they pass**

```bash
pytest tests/test_planner.py -v
```
Expected: all PASS.

**Step 7: Commit**

```bash
git add app/planner.py app/main.py tests/test_planner.py
git commit -m "feat: pass remote skills to Planner system prompt"
```

---

## Task 6: Frontend — remote badge, hide edit/delete, sync button

**Files:**
- Modify: `frontend/src/components/SkillsModal.jsx`
- Modify: `frontend/src/App.css` (or wherever skill-card styles live — check `App.css`)

**Step 1: Read the current CSS** in `frontend/src/App.css` to understand existing class names before making changes.

**Step 2: Update `SkillsModal.jsx`**

2a. In `loadSettings()`, capture `skills_repo_configured`:
```javascript
const [skillsRepoConfigured, setSkillsRepoConfigured] = useState(false)

// inside loadSettings():
if (typeof data.skills_repo_configured === 'boolean') {
  setSkillsRepoConfigured(data.skills_repo_configured)
}
```

2b. Add a `syncing` state and `syncRemoteSkills` function:
```javascript
const [syncing, setSyncing] = useState(false)

async function syncRemoteSkills() {
  setSyncing(true)
  try {
    const res = await fetch('/api/skills/sync', { method: 'POST' })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'HTTP ' + res.status)
    }
    await loadSkills()
  } catch (e) { alert('Sync failed: ' + e.message) }
  finally { setSyncing(false) }
}
```

2c. In the skills list header area (near the `+ Add skill` button), add the sync button conditionally:
```jsx
{skillsRepoConfigured && (
  <button className="btn-sm btn-secondary" onClick={syncRemoteSkills} disabled={syncing}>
    {syncing ? 'Syncing…' : 'Sync remote skills'}
  </button>
)}
```

2d. In the skill card rendering, conditionally hide Edit and Delete for remote skills, and add a badge:
```jsx
skills.map(skill => (
  <div key={skill.id} className="skill-card">
    <div className="skill-info">
      <div className="skill-name">
        {skill.name}
        {skill.source === 'remote' && (
          <span className="remote-badge">remote</span>
        )}
      </div>
      <div className="skill-desc">{skill.description}</div>
      <span className={'policy-badge ' + (skill.policy ? 'has-policy' : 'no-policy')}>
        {skill.policy ? 'policy set' : 'no policy'}
      </span>
    </div>
    <div className="skill-actions">
      <button
        className={'toggle-enabled' + (skill.enabled ? ' on' : '')}
        onClick={() => toggleSkill(skill)}
        disabled={skill.source === 'remote'}
      >
        {skill.enabled ? 'enabled' : 'disabled'}
      </button>
      {skill.source !== 'remote' && (
        <>
          <button className="btn-sm btn-secondary" onClick={() => showSkillForm(skill)}>Edit</button>
          <button className="btn-sm btn-danger" onClick={() => deleteSkill(skill.id)}>Del</button>
        </>
      )}
    </div>
  </div>
))
```

**Step 3: Add CSS for `.remote-badge`**

Add to `frontend/src/App.css`:
```css
.remote-badge {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 0.65rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  background: #1f3a5f;
  color: #58a6ff;
  vertical-align: middle;
}
```

**Step 4: Build the frontend**

```bash
npm run build
```
Expected: build succeeds with no errors.

(Run from `frontend/` directory using `workdir="frontend"`.)

**Step 5: Commit**

```bash
git add frontend/src/components/SkillsModal.jsx frontend/src/App.css
git commit -m "feat: show remote badge, hide edit/delete for remote skills, add sync button"
```

---

## Task 7: Full integration smoke test and final verification

**Step 1: Run the complete test suite**

```bash
pytest -v
```
Expected: all tests PASS, no regressions.

**Step 2: Verify the `.env.example` has the new variables**

Check that `.env.example` documents `SKILLS_REPO_URL` and `SKILLS_REPO_BRANCH`.

**Step 3: Commit any remaining changes**

```bash
git add -A
git status
# commit only if there are uncommitted changes
git commit -m "chore: final cleanup for remote git skills feature"
```

---

## Summary of all files changed

| File | Change |
|---|---|
| `app/skills.py` | Add `source` field to `Skill`; add `RemoteSkillRepository` class |
| `app/config.py` | Add `skills_repo_url`, `skills_repo_branch` to `Settings` |
| `app/main.py` | Init remote repo in lifespan; `_all_skills()` helper; update `list_skills`, `patch_skill`, `delete_skill`; add `sync` endpoint; update `SettingsResponse` and `get_settings`; pass remote skills to `_build_workflow` |
| `app/planner.py` | Accept `remote_skills` parameter; include in system prompt |
| `tests/test_skills.py` | Add `source` field tests; add remote overlay API tests |
| `tests/test_remote_skills.py` | New — tests for `RemoteSkillRepository` |
| `tests/test_config.py` | Add settings field tests |
| `tests/test_planner.py` | Add remote skills prompt test |
| `frontend/src/components/SkillsModal.jsx` | Remote badge, hide edit/delete, sync button |
| `frontend/src/App.css` | `.remote-badge` style |
| `.env.example` | Document new env vars |
