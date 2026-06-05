# Run Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist all chat history (runs) to `runs.json` so conversations survive server restarts.

**Architecture:** A `RunRepository` class in `app/runs.py` mirrors `SkillRepository` in `app/skills.py`. It loads runs from JSON on startup (recovering crashed runs to `failed`), and saves on every state change (write-through). The existing `_runs` dict in `app/main.py` remains the in-memory source of truth; `RunRepository` is the persistence layer beneath it.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / pytest / threading.Lock

---

### Task 1: Add `runs_file` setting to `app/config.py`

**Files:**
- Modify: `app/config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py` (create if missing):

```python
from app.config import Settings

def test_runs_file_default():
    s = Settings(policy_path="/tmp/policy")
    assert str(s.runs_file) == "runs.json"
```

**Step 2: Run test to verify it fails**

```
pytest tests/test_config.py::test_runs_file_default -v
```

Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'runs_file'`

**Step 3: Add the field**

In `app/config.py`, after `skills_file`:

```python
    runs_file: Path = Path("./runs.json")
```

**Step 4: Run test to verify it passes**

```
pytest tests/test_config.py::test_runs_file_default -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: add runs_file setting to config"
```

---

### Task 2: Create `app/runs.py` with `RunRepository`

**Files:**
- Create: `app/runs.py`
- Create: `tests/test_runs.py`

#### 2a – Startup: missing file → empty, logs warning

**Step 1: Write the failing tests**

```python
# tests/test_runs.py
from __future__ import annotations

import json
import logging

import pytest

from app.runs import RunRepository
from app.models import RunContext


@pytest.fixture
def runs_file(tmp_path):
    return tmp_path / "runs.json"


@pytest.fixture
def repo(runs_file):
    return RunRepository(runs_file)


def test_starts_empty_when_file_missing(runs_file):
    repo = RunRepository(runs_file)
    assert repo.list() == []


def test_missing_file_logs_warning(runs_file, caplog):
    with caplog.at_level(logging.WARNING, logger="app.runs"):
        RunRepository(runs_file)
    assert "runs.json" in caplog.text.lower() or "runs" in caplog.text.lower()


def test_corrupt_file_raises_value_error(tmp_path):
    bad = tmp_path / "runs.json"
    bad.write_text("not json {{{")
    with pytest.raises(ValueError, match="not valid JSON"):
        RunRepository(bad)
```

**Step 2: Run tests to verify they fail**

```
pytest tests/test_runs.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.runs'`

**Step 3: Create minimal `app/runs.py`**

```python
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.models import RunContext

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"done", "failed", "policy_denied"}


class RunRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._runs: dict[str, RunContext] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning(
                "runs.json not found at %s — starting with empty runs list.",
                self._path,
            )
            return
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"runs.json is not valid JSON: {exc}") from exc

        for raw in data:
            # Restart recovery: runs that were in-flight at shutdown can never resume
            if raw.get("status") in ("running", "waiting_approval"):
                raw["status"] = "failed"
                raw["final_message"] = "Server restarted."
                raw["pending_command"] = None
            ctx = RunContext.model_validate(raw)
            self._runs[ctx.run_id] = ctx

    def _save_all(self) -> None:
        rows = []
        for ctx in self._runs.values():
            d = ctx.model_dump(mode="json")
            d["pending_command"] = None  # transient — never persist
            rows.append(d)
        self._path.write_text(json.dumps(rows, indent=2))

    def save(self, ctx: RunContext) -> None:
        """Persist a single run (upsert). Call this under _runs_lock in main.py."""
        with self._lock:
            # Keep our internal dict in sync so list()/get() are accurate
            self._runs[ctx.run_id] = ctx
            self._save_all()

    def list(self) -> list[RunContext]:
        with self._lock:
            return [ctx.model_copy() for ctx in self._runs.values()]

    def get(self, run_id: str) -> RunContext:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return self._runs[run_id].model_copy()

    def all_as_dict(self) -> dict[str, RunContext]:
        """Return a shallow copy of the internal dict for populating _runs at startup."""
        with self._lock:
            return dict(self._runs)
```

**Step 4: Run tests to verify they pass**

```
pytest tests/test_runs.py -v
```

Expected: the three tests above PASS

#### 2b – save / list / get / round-trip tests

**Step 1: Add more tests to `tests/test_runs.py`**

```python
def _make_run(run_id="r1", status="done", final_message="ok") -> RunContext:
    return RunContext(run_id=run_id, prompt="test", status=status, final_message=final_message)


def test_save_and_get(repo):
    ctx = _make_run()
    repo.save(ctx)
    fetched = repo.get("r1")
    assert fetched.run_id == "r1"
    assert fetched.status == "done"


def test_save_persists_to_file(repo, runs_file):
    repo.save(_make_run())
    data = json.loads(runs_file.read_text())
    assert len(data) == 1
    assert data[0]["run_id"] == "r1"


def test_list_returns_all_saved_runs(repo):
    repo.save(_make_run("a"))
    repo.save(_make_run("b"))
    ids = [r.run_id for r in repo.list()]
    assert sorted(ids) == ["a", "b"]


def test_get_unknown_raises_key_error(repo):
    with pytest.raises(KeyError):
        repo.get("nonexistent")


def test_json_round_trip(tmp_path):
    path = tmp_path / "runs.json"
    repo1 = RunRepository(path)
    ctx = _make_run(status="done", final_message="all good")
    repo1.save(ctx)

    repo2 = RunRepository(path)
    loaded = repo2.get("r1")
    assert loaded.status == "done"
    assert loaded.final_message == "all good"
    assert loaded.prompt == "test"


def test_pending_command_not_persisted(tmp_path):
    path = tmp_path / "runs.json"
    repo1 = RunRepository(path)
    ctx = _make_run()
    ctx.pending_command = {"argv": ["ls"], "rationale": "check"}
    repo1.save(ctx)

    data = json.loads(path.read_text())
    assert data[0]["pending_command"] is None


def test_restart_recovery_running_to_failed(tmp_path):
    path = tmp_path / "runs.json"
    # Write a runs.json as if server was killed mid-run
    path.write_text(json.dumps([{
        "run_id": "r1", "prompt": "p", "history": [],
        "status": "running", "final_message": None,
        "pending_command": None, "parent_run_id": None,
        "skill_overrides": {},
    }]))
    repo = RunRepository(path)
    ctx = repo.get("r1")
    assert ctx.status == "failed"
    assert ctx.final_message == "Server restarted."


def test_restart_recovery_waiting_approval_to_failed(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps([{
        "run_id": "r1", "prompt": "p", "history": [],
        "status": "waiting_approval", "final_message": None,
        "pending_command": {"argv": ["ls"], "rationale": "x"},
        "parent_run_id": None, "skill_overrides": {},
    }]))
    repo = RunRepository(path)
    ctx = repo.get("r1")
    assert ctx.status == "failed"
    assert ctx.final_message == "Server restarted."
    assert ctx.pending_command is None


def test_terminal_runs_survive_restart(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps([{
        "run_id": "r1", "prompt": "p", "history": [],
        "status": "done", "final_message": "great",
        "pending_command": None, "parent_run_id": None,
        "skill_overrides": {},
    }]))
    repo = RunRepository(path)
    ctx = repo.get("r1")
    assert ctx.status == "done"
    assert ctx.final_message == "great"
```

**Step 2: Run all tests in the file**

```
pytest tests/test_runs.py -v
```

Expected: all PASS

**Step 3: Commit**

```bash
git add app/runs.py tests/test_runs.py
git commit -m "feat: add RunRepository for run persistence"
```

---

### Task 3: Wire `RunRepository` into `app/main.py`

This task integrates the repository: populate `_runs` at startup, expose `run_repo` on `app.state`, and call `run_repo.save(ctx)` at every state-change point.

**Files:**
- Modify: `app/main.py`

State-change points to save after:
1. **Run created** — after `_runs[run_id] = ctx` in `start_run()`
2. **Workflow complete** — after `_runs[run_id] = result` in `_execute()`
3. **Skill override PATCH** — after `ctx.skill_overrides[skill_id] = body.enabled` in `patch_run_skill()`

Note: The approval gate modifies `ctx.status` and `ctx.pending_command` directly on the shared object. `pending_command` is intentionally NOT persisted (the repo always writes `None` for it). We do NOT need to save during the approval wait — the run is saved on completion by `_execute()`.

**Step 1: Write integration tests that will fail (no persistence yet)**

Add to `tests/test_runs.py`:

```python
# Integration: persistence wired into the app
import threading
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from app.main import app, _runs, _runs_lock


@pytest.fixture
def client_with_repo(tmp_path):
    from app.runs import RunRepository
    from app.skills import SkillRepository
    repo = RunRepository(tmp_path / "runs.json")
    app.state.run_repo = repo
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    # Clear in-memory runs between tests
    with _runs_lock:
        _runs.clear()
        _runs.update(repo.all_as_dict())
    return TestClient(app), repo


def test_run_persisted_on_creation(client_with_repo, tmp_path):
    client, repo = client_with_repo
    with patch("app.main.AgentWorkflow") as MockWF:
        from app.models import RunContext
        mock_wf = MagicMock()
        MockWF.return_value = mock_wf
        # workflow.run() returns a completed context
        def fake_run(prompt, max_iterations, ctx):
            ctx.status = "done"
            ctx.final_message = "finished"
            return ctx
        mock_wf.run.side_effect = fake_run
        res = client.post("/api/agent/run", json={"prompt": "hello"})
        assert res.status_code == 202
        run_id = res.json()["run_id"]
        # Give thread time to complete
        import time; time.sleep(0.2)
        loaded = repo.get(run_id)
        assert loaded.status == "done"


def test_skill_override_persisted(client_with_repo, tmp_path):
    client, repo = client_with_repo
    from app.skills import SkillRepository
    skill_repo = SkillRepository(tmp_path / "skills.json")
    skill = skill_repo.create(name="ls", description="list")
    app.state.skill_repo = skill_repo

    # Manually add a run to _runs and repo
    from app.models import RunContext
    ctx = RunContext(run_id="test-run", prompt="p", status="done")
    with _runs_lock:
        _runs["test-run"] = ctx
    repo.save(ctx)

    res = client.patch("/api/agent/runs/test-run/skills/" + skill.id, json={"enabled": False})
    assert res.status_code == 200
    loaded = repo.get("test-run")
    assert loaded.skill_overrides[skill.id] is False
```

**Step 2: Run the new integration tests to confirm they fail**

```
pytest tests/test_runs.py::test_run_persisted_on_creation tests/test_runs.py::test_skill_override_persisted -v
```

Expected: FAIL (no `app.state.run_repo` wired in yet)

**Step 3: Modify `app/main.py`**

3a. Add import at top:
```python
from app.runs import RunRepository
```

3b. Replace the `lifespan` function:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.skill_repo = SkillRepository(settings.skills_file)
    run_repo = RunRepository(settings.runs_file)
    app.state.run_repo = run_repo
    # Restore persisted runs into the in-memory dict
    with _runs_lock:
        _runs.update(run_repo.all_as_dict())
    yield
```

3c. In `start_run()`, after `_runs[run_id] = ctx`, add a save:
```python
    with _runs_lock:
        _runs[run_id] = ctx
    req.app.state.run_repo.save(ctx)  # persist creation
```

3d. In the `_execute()` closure inside `start_run()`, capture `run_repo` and save after the workflow completes:
```python
    run_repo = req.app.state.run_repo  # capture before thread

    def _execute() -> None:
        workflow = _build_workflow(skill_repo, run_id=run_id, ctx=ctx)
        result = workflow.run(
            prompt=request.prompt,
            max_iterations=settings.max_iterations,
            ctx=ctx,
        )
        with _runs_lock:
            _runs[run_id] = result
        run_repo.save(result)  # persist final state
```

3e. In `patch_run_skill()`, add a save after updating `skill_overrides`:
```python
    with _runs_lock:
        ctx.skill_overrides[skill_id] = body.enabled
    req.app.state.run_repo.save(ctx)  # persist override
    return _run_skill_response(skill, ctx.skill_overrides)
```

**Step 4: Run integration tests**

```
pytest tests/test_runs.py -v
```

Expected: all PASS

**Step 5: Run full test suite**

```
pytest -q
```

Expected: all 136+ tests PASS (no regressions)

**Step 6: Commit**

```bash
git add app/main.py app/runs.py tests/test_runs.py
git commit -m "feat: wire RunRepository into lifespan and save on state changes"
```

---

### Task 4: Startup population test (end-to-end restart simulation)

This verifies that after a "restart" (new `RunRepository` instance over the same file), `GET /api/agent/runs` returns the persisted runs.

**Files:**
- Modify: `tests/test_runs.py`

**Step 1: Add the test**

```python
def test_runs_survive_restart(tmp_path):
    """After saving a completed run and re-loading, GET /api/agent/runs shows it."""
    from app.runs import RunRepository
    from app.skills import SkillRepository
    from app.models import RunContext

    runs_path = tmp_path / "runs.json"

    # First "session": save a completed run
    repo1 = RunRepository(runs_path)
    ctx = RunContext(run_id="r-persist", prompt="survives?", status="done", final_message="yes")
    repo1.save(ctx)

    # Second "session": simulate restart by creating a new repo + populating _runs
    repo2 = RunRepository(runs_path)
    app.state.run_repo = repo2
    app.state.skill_repo = SkillRepository(tmp_path / "skills.json")
    with _runs_lock:
        _runs.clear()
        _runs.update(repo2.all_as_dict())

    client = TestClient(app)
    res = client.get("/api/agent/runs")
    assert res.status_code == 200
    ids = [r["run_id"] for r in res.json()]
    assert "r-persist" in ids
```

**Step 2: Run it**

```
pytest tests/test_runs.py::test_runs_survive_restart -v
```

Expected: PASS

**Step 3: Run the full suite once more**

```
pytest -q
```

Expected: all PASS

**Step 4: Commit**

```bash
git add tests/test_runs.py
git commit -m "test: verify runs survive server restart"
```

---

### Task 5: Final verification and finishing

**Step 1: Run the full test suite**

```
pytest -q
```

Expected: all tests PASS, no regressions vs. the original 136.

**Step 2: Manual smoke check (optional but recommended)**

```bash
uvicorn app.main:app --reload
# POST /api/agent/run with {"prompt": "list files"}
# kill server, restart
# GET /api/agent/runs — prior run should appear
```

**Step 3: Invoke finishing-a-development-branch skill**

Use `superpowers:finishing-a-development-branch` to decide how to integrate (merge/PR/cleanup).

---

## Quick Reference

### Key invariants
- `pending_command` is **never** written to disk — always serialised as `None`
- `running` / `waiting_approval` at load → rewritten to `failed` with `final_message = "Server restarted."`
- `_runs` dict is the in-memory source of truth; `RunRepository` is write-through underneath
- All saves go through `run_repo.save(ctx)`, which is lock-protected inside `RunRepository`
- `_runs_lock` in `main.py` guards the in-memory dict; `RunRepository._lock` guards the file write — both are needed

### File map
| File | Change |
|------|--------|
| `app/config.py` | Add `runs_file: Path = Path("./runs.json")` |
| `app/runs.py` | New — `RunRepository` class |
| `app/main.py` | Import `RunRepository`; wire in `lifespan`; three `run_repo.save()` calls |
| `tests/test_runs.py` | New — unit + integration tests |
| `tests/test_config.py` | Add `test_runs_file_default` |
