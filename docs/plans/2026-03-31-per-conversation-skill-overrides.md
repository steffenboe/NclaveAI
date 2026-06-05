# Per-Conversation Skill Overrides Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to enable/disable skills on a per-conversation (per-run) basis, overriding each skill's global enabled state for the lifetime of that run.

**Architecture:** Add `skill_overrides: dict[str, bool]` to `RunContext` (sparse map: skill ID → effective enabled for this run). `PolicyEvaluator` receives all skills (not pre-filtered) and resolves each skill's effective enabled state at evaluation time by consulting the override map. Two new API endpoints (`GET` and `PATCH` on `/api/agent/runs/{run_id}/skills`) expose the per-run skill state. The UI adds a per-conversation skill toggle panel in the main conversation area.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / pytest / vanilla JS (no framework)

---

## Task 1: Add `skill_overrides` to `RunContext`

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_run_context_has_empty_skill_overrides_by_default():
    ctx = RunContext(run_id="r1", prompt="hello")
    assert ctx.skill_overrides == {}


def test_run_context_skill_overrides_stores_bool_by_skill_id():
    ctx = RunContext(run_id="r1", prompt="hello", skill_overrides={"skill-1": False, "skill-2": True})
    assert ctx.skill_overrides["skill-1"] is False
    assert ctx.skill_overrides["skill-2"] is True


def test_run_context_skill_overrides_serializes_to_json():
    ctx = RunContext(run_id="r1", prompt="hello", skill_overrides={"abc": True})
    data = ctx.model_dump()
    assert data["skill_overrides"] == {"abc": True}
```

**Step 2: Run test to verify it fails**

```
pytest tests/test_models.py -v -k "skill_override"
```

Expected: `AttributeError` or similar — field does not exist yet.

**Step 3: Write minimal implementation**

In `app/models.py`, add one field to `RunContext`:

```python
class RunContext(BaseModel):
    run_id: str
    prompt: str
    history: list[ActionResult] = []
    status: Literal["running", "done", "failed", "policy_denied", "waiting_approval"] = "running"
    final_message: str | None = None
    pending_command: dict[str, Any] | None = None
    parent_run_id: str | None = None
    skill_overrides: dict[str, bool] = {}
```

**Step 4: Run tests to verify they pass**

```
pytest tests/test_models.py -v -k "skill_override"
```

Expected: PASS (3 tests)

**Step 5: Run full test suite to catch regressions**

```
pytest -x -q
```

Expected: all existing tests pass.

**Step 6: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add skill_overrides field to RunContext"
```

---

## Task 2: Update `PolicyEvaluator` to accept and apply per-run overrides

**Files:**
- Modify: `app/policy.py`
- Modify: `app/main.py` (update `_build_workflow`)
- Test: `tests/test_policy.py`

### Background

Currently `PolicyEvaluator.__init__` receives a pre-filtered list of enabled skills. We need it to receive **all** skills (so disabled-globally-but-overridden-locally skills can be used) and resolve each skill's effective enabled state at `evaluate()` call time.

The `_skill_interps` list needs to carry the skill ID alongside the name and interpreter, so override resolution can match by ID.

The existing `_skill` helper in `tests/test_policy.py` uses `id="test-id"` — we can extend it.

**Step 1: Write the failing tests**

Add to `tests/test_policy.py`:

```python
def _skill_with_id(skill_id: str, policy: str | None, enabled: bool = True) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        description="test skill",
        enabled=enabled,
        policy=policy,
        created_at=datetime.now(timezone.utc),
    )


# ── skill_overrides tests ──────────────────────────────────────────────────────

def test_override_enables_globally_disabled_skill():
    """A globally-disabled skill can be enabled for a specific run via overrides."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=False)
    evaluator = PolicyEvaluator(skills=[skill])
    # Without override: globally disabled → must not allow (falls through to global deny-all)
    allowed, _, _ = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is False

    # With override enabling it: must now allow
    allowed, _, skill_name = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides={"s1": True},
    )
    assert allowed is True
    assert skill_name == "s1"


def test_override_disables_globally_enabled_skill(tmp_path):
    """A globally-enabled skill can be disabled for a specific run via overrides."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=True)
    evaluator = PolicyEvaluator(
        policy_path=_allow_all_policy(tmp_path),
        skills=[skill],
    )
    # Without override: globally enabled → allows
    allowed, _, skill_name = evaluator.evaluate(_cmd(["kubectl", "get", "pods"]))
    assert allowed is True
    assert skill_name == "s1"

    # With override disabling it: must fall through to global (allow-all in this test)
    allowed, _, skill_name = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides={"s1": False},
    )
    assert allowed is True   # falls through to global allow-all
    assert skill_name is None  # global fallback, not the skill


def test_unknown_skill_id_in_overrides_is_ignored():
    """Unknown skill IDs in the overrides map must not cause errors."""
    evaluator = PolicyEvaluator()  # no skills
    allowed, _, _ = evaluator.evaluate(
        _cmd(["ls"]),
        skill_overrides={"nonexistent-id": True},
    )
    # executor.rego allows ls, so this should still pass via global
    assert allowed is True


def test_no_overrides_falls_back_to_global_enabled_flag():
    """When skill_overrides is None, global enabled flag is used (existing behaviour)."""
    skill = _skill_with_id("s1", 'allow { input.argv[0] == "kubectl" }', enabled=True)
    evaluator = PolicyEvaluator(skills=[skill])
    allowed, _, skill_name = evaluator.evaluate(
        _cmd(["kubectl", "get", "pods"]),
        skill_overrides=None,
    )
    assert allowed is True
    assert skill_name == "s1"
```

**Step 2: Run tests to verify they fail**

```
pytest tests/test_policy.py -v -k "override"
```

Expected: FAIL — `evaluate()` does not accept `skill_overrides` parameter yet.

**Step 3: Implement the changes in `app/policy.py`**

Replace the entire `app/policy.py` with:

```python
from __future__ import annotations

import json
from pathlib import Path

from regopy import Interpreter

from app.config import settings
from app.models import Command
from app.skills import Skill


class PolicyEvaluator:
    def __init__(
        self,
        policy_path: Path | None = None,
        skills: list[Skill] | None = None,
    ) -> None:
        path = policy_path or settings.policy_path
        self._global_interp = Interpreter()
        self._global_interp.add_module("executor", path.read_text())

        # Store (skill_id, skill_name, enabled_globally, interp) for ALL skills with a policy.
        # Enabled-state filtering now happens at evaluate() time so per-run overrides can work.
        skill_list = skills if skills is not None else []
        self._skill_interps: list[tuple[str, str, bool, Interpreter]] = []
        for skill in skill_list:
            if skill.policy is not None:
                interp = Interpreter()
                interp.add_module("skill", f"package ops.agent\n{skill.policy}")
                self._skill_interps.append((skill.id, skill.name, skill.enabled, interp))

    def evaluate(
        self,
        command: Command,
        skill_overrides: dict[str, bool] | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """
        Returns (allowed, reason, skill_name).
        skill_name is the name of the skill that permitted the command,
        or None when the global policy is used or the command is denied.

        Evaluation order:
        - Skill policies are checked first; any skill that is effectively enabled
          (global flag overridden by skill_overrides where present) can allow the command.
        - If no enabled skill's policy allows the command, fall back to the global policy.

        skill_overrides: mapping of skill_id → effective enabled state for this run.
          If a skill ID is absent from the map, the skill's global enabled flag is used.
          Pass None to use global flags for all skills (equivalent to an empty dict).
        """
        overrides = skill_overrides or {}
        input_json = json.dumps({"argv": command.argv})

        for skill_id, skill_name, global_enabled, interp in self._skill_interps:
            effective_enabled = overrides.get(skill_id, global_enabled)
            if not effective_enabled:
                continue
            interp.set_input_term(input_json)
            out = interp.query("data.ops.agent.allow")
            if out.ok() and '"expressions":[true]' in str(out):
                return True, None, skill_name

        # Fallback: no skill claimed this command — use the global policy
        self._global_interp.set_input_term(input_json)
        output = self._global_interp.query("data.ops.agent.allow")
        if output.ok() and '"expressions":[true]' in str(output):
            return True, None, None
        return False, f"Command {command.argv[0]!r} denied by policy", None
```

**Step 4: Update `_build_workflow` in `app/main.py`**

Change line 93 from:
```python
enabled_skills = [s for s in skill_repo.list() if s.enabled]
```
to:
```python
all_skills = skill_repo.list()
```

And update the `PolicyEvaluator` instantiation to pass `all_skills` instead of `enabled_skills`:
```python
return AgentWorkflow(
    planner=Planner(skill_repo),
    policy=PolicyEvaluator(skills=all_skills),
    executor=CommandExecutor(),
    approval_gate=gate,
)
```

The full updated `_build_workflow`:

```python
def _build_workflow(
    skill_repo: SkillRepository,
    run_id: str | None = None,
    ctx: RunContext | None = None,
) -> AgentWorkflow:
    all_skills = skill_repo.list()
    gate = None
    if run_id is not None and ctx is not None:
        with _settings_lock:
            need_approval = _approval_required
        if need_approval:
            gate = _make_approval_gate(run_id, ctx)
    return AgentWorkflow(
        planner=Planner(skill_repo),
        policy=PolicyEvaluator(skills=all_skills),
        executor=CommandExecutor(),
        approval_gate=gate,
    )
```

**Step 5: Run the new tests**

```
pytest tests/test_policy.py -v -k "override"
```

Expected: PASS (4 tests)

**Step 6: Run full policy test suite**

```
pytest tests/test_policy.py -v
```

Expected: all existing tests pass (no regressions — existing tests do not pass `skill_overrides` so they use the default `None` / global-flag behaviour).

**Step 7: Commit**

```bash
git add app/policy.py app/main.py tests/test_policy.py
git commit -m "feat: support per-run skill_overrides in PolicyEvaluator"
```

---

## Task 3: Pass `skill_overrides` from `RunContext` into `policy.evaluate()`

**Files:**
- Modify: `app/workflow.py`
- Test: `tests/test_workflow.py`

### Background

`AgentWorkflow.run()` calls `self._policy.evaluate(command)` on line 70. It needs to pass `ctx.skill_overrides` so overrides are applied for that run.

**Step 1: Look at the existing workflow tests**

Read `tests/test_workflow.py` to understand the fixture/mock pattern before writing tests.

**Step 2: Write the failing test**

Add to `tests/test_workflow.py` (after reading the file to understand the existing pattern):

```python
def test_workflow_passes_skill_overrides_to_policy(mock_planner, mock_executor, mock_policy):
    """workflow must pass ctx.skill_overrides into policy.evaluate() on each call."""
    mock_planner.next_action.side_effect = [
        PlannerOutput(status="action", command=Command(argv=["kubectl", "get", "pods"], rationale="r"), summary="s"),
        PlannerOutput(status="done", summary="all done"),
    ]
    mock_policy.evaluate.return_value = (True, None, "kubectl-skill")
    mock_executor.run.return_value = ActionResult(
        command=Command(argv=["kubectl", "get", "pods"], rationale="r"),
        allowed=True, stdout="", stderr="", exit_code=0,
    )

    ctx = RunContext(
        run_id="r1",
        prompt="test",
        skill_overrides={"skill-abc": False},
    )
    wf = AgentWorkflow(planner=mock_planner, policy=mock_policy, executor=mock_executor)
    wf.run(prompt="test", ctx=ctx)

    mock_policy.evaluate.assert_called_once_with(
        Command(argv=["kubectl", "get", "pods"], rationale="r"),
        skill_overrides={"skill-abc": False},
    )
```

**Step 3: Run test to verify it fails**

```
pytest tests/test_workflow.py -v -k "skill_overrides"
```

Expected: FAIL — `evaluate` is called without `skill_overrides` keyword argument.

**Step 4: Update `AgentWorkflow.run()` in `app/workflow.py`**

Change line 70 from:
```python
allowed, reason, skill_name = self._policy.evaluate(command)
```
to:
```python
allowed, reason, skill_name = self._policy.evaluate(command, skill_overrides=ctx.skill_overrides)
```

**Step 5: Run test to verify it passes**

```
pytest tests/test_workflow.py -v -k "skill_overrides"
```

Expected: PASS

**Step 6: Run full workflow test suite**

```
pytest tests/test_workflow.py -v
```

Expected: all pass.

**Step 7: Commit**

```bash
git add app/workflow.py tests/test_workflow.py
git commit -m "feat: pass skill_overrides from RunContext into policy.evaluate()"
```

---

## Task 4: Add API endpoints for per-run skill overrides

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_run_skills.py`

### New endpoints

**`GET /api/agent/runs/{run_id}/skills`**  
Returns each skill in the global registry annotated with its `effective_enabled` for that run.

Response shape (list of objects):
```json
[
  {
    "id": "...",
    "name": "kubectl",
    "enabled": true,
    "effective_enabled": false,
    "description": "...",
    "policy": "...",
    "created_at": "..."
  }
]
```

**`PATCH /api/agent/runs/{run_id}/skills/{skill_id}`**  
Body: `{ "enabled": true | false }`  
Sets `ctx.skill_overrides[skill_id] = enabled`.  
Returns same shape as `GET` for the single updated skill.

**Step 1: Write the failing tests**

Create `tests/test_run_skills.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app, _runs, _runs_lock
from app.models import RunContext
from app.skills import SkillRepository


@pytest.fixture(autouse=True)
def clear_runs():
    """Ensure _runs is empty before each test."""
    with _runs_lock:
        _runs.clear()
    yield
    with _runs_lock:
        _runs.clear()


@pytest.fixture
def client(tmp_path):
    repo = SkillRepository(tmp_path / "skills.json")
    app.state.skill_repo = repo
    return TestClient(app)


@pytest.fixture
def skill_and_run(client):
    """Create one skill and one run, returning (skill_id, run_id)."""
    skill_res = client.post("/api/skills", json={"name": "kubectl", "description": "k8s cli"})
    assert skill_res.status_code == 201
    skill_id = skill_res.json()["id"]

    ctx = RunContext(run_id="test-run-1", prompt="hello")
    with _runs_lock:
        _runs["test-run-1"] = ctx

    return skill_id, "test-run-1"


# ── GET /api/agent/runs/{run_id}/skills ───────────────────────────────────────

def test_get_run_skills_returns_skill_list(client, skill_and_run):
    skill_id, run_id = skill_and_run
    res = client.get(f"/api/agent/runs/{run_id}/skills")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == skill_id
    assert data[0]["name"] == "kubectl"


def test_get_run_skills_includes_effective_enabled(client, skill_and_run):
    skill_id, run_id = skill_and_run
    res = client.get(f"/api/agent/runs/{run_id}/skills")
    assert res.status_code == 200
    item = res.json()[0]
    assert "effective_enabled" in item
    assert item["effective_enabled"] == item["enabled"]  # no override yet


def test_get_run_skills_reflects_override(client, skill_and_run):
    skill_id, run_id = skill_and_run
    # Apply override
    patch_res = client.patch(
        f"/api/agent/runs/{run_id}/skills/{skill_id}",
        json={"enabled": False},
    )
    assert patch_res.status_code == 200
    # Now GET should show effective_enabled=False even though global enabled=True
    res = client.get(f"/api/agent/runs/{run_id}/skills")
    assert res.status_code == 200
    item = res.json()[0]
    assert item["enabled"] is True          # global unchanged
    assert item["effective_enabled"] is False  # override applied


def test_get_run_skills_404_for_unknown_run(client):
    res = client.get("/api/agent/runs/nonexistent-run/skills")
    assert res.status_code == 404


# ── PATCH /api/agent/runs/{run_id}/skills/{skill_id} ─────────────────────────

def test_patch_run_skill_sets_override(client, skill_and_run):
    skill_id, run_id = skill_and_run
    res = client.patch(
        f"/api/agent/runs/{run_id}/skills/{skill_id}",
        json={"enabled": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == skill_id
    assert data["effective_enabled"] is False
    assert data["enabled"] is True  # global unchanged


def test_patch_run_skill_override_enables_disabled_skill(client, tmp_path):
    """Globally disabled skill can be enabled per-run."""
    repo = SkillRepository(tmp_path / "skills.json")
    app.state.skill_repo = repo
    c = TestClient(app)

    skill_res = c.post("/api/skills", json={"name": "gh", "description": "GitHub CLI", "enabled": False})
    skill_id = skill_res.json()["id"]

    ctx = RunContext(run_id="run-2", prompt="test")
    with _runs_lock:
        _runs["run-2"] = ctx

    res = c.patch(f"/api/agent/runs/run-2/skills/{skill_id}", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["effective_enabled"] is True
    assert res.json()["enabled"] is False  # global still False


def test_patch_run_skill_404_for_unknown_run(client, skill_and_run):
    skill_id, _ = skill_and_run
    res = client.patch(f"/api/agent/runs/no-such-run/skills/{skill_id}", json={"enabled": False})
    assert res.status_code == 404


def test_patch_run_skill_404_for_unknown_skill(client, skill_and_run):
    _, run_id = skill_and_run
    res = client.patch(f"/api/agent/runs/{run_id}/skills/no-such-skill", json={"enabled": False})
    assert res.status_code == 404


def test_patch_run_skill_persists_in_run_context(client, skill_and_run):
    skill_id, run_id = skill_and_run
    client.patch(f"/api/agent/runs/{run_id}/skills/{skill_id}", json={"enabled": False})
    with _runs_lock:
        ctx = _runs[run_id]
    assert ctx.skill_overrides[skill_id] is False
```

**Step 2: Run tests to verify they fail**

```
pytest tests/test_run_skills.py -v
```

Expected: 404 for all — endpoints do not exist yet.

**Step 3: Implement the endpoints in `app/main.py`**

Add a response model at module level (alongside existing Pydantic models):

```python
class RunSkillResponse(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    effective_enabled: bool
    policy: str | None
    created_at: Any
```

Add two endpoint functions. Place them in the Skills section of `main.py` (after the existing skills endpoints, before `# ── Misc`):

```python
# ── Per-run skill overrides ───────────────────────────────────────────────────

class RunSkillPatchRequest(BaseModel):
    enabled: bool


def _run_skill_response(skill, overrides: dict[str, bool]) -> RunSkillResponse:
    return RunSkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        enabled=skill.enabled,
        effective_enabled=overrides.get(skill.id, skill.enabled),
        policy=skill.policy,
        created_at=skill.created_at,
    )


@app.get("/api/agent/runs/{run_id}/skills")
def get_run_skills(run_id: str, request: Request) -> list[RunSkillResponse]:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    skills = request.app.state.skill_repo.list()
    return [_run_skill_response(s, ctx.skill_overrides) for s in skills]


@app.patch("/api/agent/runs/{run_id}/skills/{skill_id}")
def patch_run_skill(
    run_id: str,
    skill_id: str,
    body: RunSkillPatchRequest,
    request: Request,
) -> RunSkillResponse:
    with _runs_lock:
        ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    try:
        skill = request.app.state.skill_repo.get(skill_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} not found")
    with _runs_lock:
        ctx.skill_overrides[skill_id] = body.enabled
    return _run_skill_response(skill, ctx.skill_overrides)
```

**Step 4: Run the tests**

```
pytest tests/test_run_skills.py -v
```

Expected: PASS (all tests)

**Step 5: Run full test suite**

```
pytest -x -q
```

Expected: all pass.

**Step 6: Commit**

```bash
git add app/main.py tests/test_run_skills.py
git commit -m "feat: add GET/PATCH /api/agent/runs/{run_id}/skills endpoints"
```

---

## Task 5: UI — per-conversation skill toggles

**Files:**
- Modify: `app/static/index.html`

### What to build

When a conversation is selected, show a "Skills" section in the conversation header area (between the conversation feed and the input area, or as a collapsible bar above the input area). Each skill is rendered as a small toggle button showing its `effective_enabled` state for the active run. Toggling calls `PATCH /api/agent/runs/{run_id}/skills/{skill_id}`.

**Design details:**
- The section is only visible when a conversation is selected (`selectedRootId != null`)
- It fetches `GET /api/agent/runs/{run_id}/skills` where `run_id` is the tail run of the current conversation chain (same as `context_run_id` used for follow-up runs)
- It uses the existing `.toggle-enabled` / `.toggle-enabled.on` CSS classes for consistency with the global Skills modal
- A label "Skills for this conversation" distinguishes it from the global toggles
- The section re-fetches when the conversation changes (on `selectConversation()` calls)

**Step 1: Add CSS for the skills bar**

In the `<style>` block (after `.no-items-msg { ... }`), add:

```css
/* ── Per-conversation skills bar ─────────────────────── */
.conv-skills-bar {
  border-top: 1px solid #21262d;
  background: #0d1117;
  padding: 6px 32px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 36px;
}
.conv-skills-label {
  font-size: 10px;
  color: #484f58;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  flex-shrink: 0;
  white-space: nowrap;
}
.conv-skills-bar.hidden { display: none; }
```

**Step 2: Add the HTML element**

In the `<div class="main">` block, between `conversation-feed` and `input-area`, add:

```html
<!-- ── Per-conversation skills bar ────────────────────── -->
<div class="conv-skills-bar hidden" id="conv-skills-bar">
  <span class="conv-skills-label">This conversation:</span>
  <div id="conv-skills-toggles" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
</div>
```

**Step 3: Add JS functions**

Add these functions in the `<script>` block, alongside the existing Skills CRUD functions:

```javascript
// ── Per-conversation skill overrides ───────────────────
let convSkillsData = [];   // [{id, name, enabled, effective_enabled, ...}]

async function loadConvSkills(runId) {
  if (!runId) {
    document.getElementById('conv-skills-bar').classList.add('hidden');
    return;
  }
  try {
    const res = await fetch('/api/agent/runs/' + runId + '/skills');
    if (!res.ok) return;
    convSkillsData = await res.json();
    renderConvSkills(runId);
  } catch {}
}

function renderConvSkills(runId) {
  const bar = document.getElementById('conv-skills-bar');
  const container = document.getElementById('conv-skills-toggles');
  container.innerHTML = '';
  if (convSkillsData.length === 0) {
    bar.classList.add('hidden');
    return;
  }
  bar.classList.remove('hidden');
  for (const skill of convSkillsData) {
    const btn = document.createElement('button');
    btn.className = 'toggle-enabled btn-sm' + (skill.effective_enabled ? ' on' : '');
    btn.textContent = skill.name;
    btn.title = skill.description;
    btn.onclick = () => toggleConvSkill(runId, skill);
    container.appendChild(btn);
  }
}

async function toggleConvSkill(runId, skill) {
  try {
    const res = await fetch('/api/agent/runs/' + runId + '/skills/' + skill.id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !skill.effective_enabled }),
    });
    if (!res.ok) return;
    await loadConvSkills(runId);
  } catch {}
}
```

**Step 4: Wire `loadConvSkills` into `selectConversation` and `render`**

Update `selectConversation`:

```javascript
function selectConversation(rootId) {
  selectedRootId = rootId;
  render();
  // Load per-conversation skills for the tail run
  const chain = getChain(rootId);
  const tailRunId = chain[chain.length - 1];
  loadConvSkills(tailRunId);
}
```

Also call `loadConvSkills(null)` in `newChat()` to hide the bar:

```javascript
function newChat() {
  selectedRootId = null;
  loadConvSkills(null);
  render();
  document.getElementById('prompt-input').focus();
}
```

And at the end of `loadAll()` after `render()`, load conv skills for the initially selected run:

```javascript
async function loadAll() {
  await loadSettings();
  try {
    const res = await fetch('/api/agent/runs');
    if (!res.ok) return;
    const list = await res.json();
    for (const r of list) {
      upsertRun(r);
      if (r.status === 'running' || r.status === 'waiting_approval') pollRun(r.run_id);
    }
    const roots = getRoots();
    if (roots.length > 0) {
      selectedRootId = roots[roots.length - 1];
      const chain = getChain(selectedRootId);
      loadConvSkills(chain[chain.length - 1]);
    }
  } catch {}
  render();
}
```

When a new run is submitted via `submitRun()`, after polling starts, refresh the conv skills bar since the tail run ID has changed. After `pollRun(data.run_id)` add:

```javascript
loadConvSkills(data.run_id);
```

**Step 5: Manual verification**

Start the server:
```
uvicorn app.main:app --reload
```

1. Open the UI, create a skill in the global Skills modal
2. Start a new conversation
3. The skills bar should appear above the input with the skill shown as enabled
4. Toggle it off — the button should turn grey
5. Toggle it back on — the button should turn green
6. Start a new separate conversation — the override from the first conversation should not appear (fresh `skill_overrides`)

**Step 6: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add per-conversation skill toggle bar to UI"
```

---

## Task 6: Integration test — override blocks a globally-enabled skill

**Files:**
- Modify: `tests/test_workflow.py` (or `tests/test_run_skills.py`)

**Step 1: Write the test**

Add to `tests/test_workflow.py` (read the file first to understand mock patterns, then add):

```python
def test_globally_enabled_skill_blocked_by_conversation_override(mock_planner, mock_executor):
    """An override disabling a globally-enabled skill blocks commands that skill would allow."""
    from app.policy import PolicyEvaluator
    from app.skills import Skill
    from datetime import datetime, timezone

    skill = Skill(
        id="k8s",
        name="kubectl",
        description="k8s",
        enabled=True,  # globally enabled
        policy='allow { input.argv[0] == "kubectl" }',
        created_at=datetime.now(timezone.utc),
    )
    policy = PolicyEvaluator(skills=[skill])

    mock_planner.next_action.return_value = PlannerOutput(
        status="action",
        command=Command(argv=["kubectl", "get", "pods"], rationale="check pods"),
        summary="running kubectl",
    )

    ctx = RunContext(
        run_id="r-override",
        prompt="check pods",
        skill_overrides={"k8s": False},  # override: disable kubectl skill for this run
    )

    wf = AgentWorkflow(planner=mock_planner, policy=policy, executor=mock_executor)
    result = wf.run(prompt="check pods", ctx=ctx)

    # executor.rego has deny-all, so with skill disabled, command must be blocked
    assert result.status == "policy_denied"
    mock_executor.run.assert_not_called()
```

**Step 2: Run to verify it fails (before the workflow change)**

```
pytest tests/test_workflow.py -v -k "blocked_by_conversation_override"
```

Note: this test should already pass after Task 3 is complete. If it does, great. If not, check that `ctx.skill_overrides` is being passed correctly.

**Step 3: Run the full test suite one final time**

```
pytest -q
```

Expected: all tests pass.

**Step 4: Commit**

```bash
git add tests/test_workflow.py
git commit -m "test: add integration test for conversation-level skill override blocking"
```

---

## Final verification

Run the complete test suite and confirm everything passes:

```
pytest -v
```

Then do a quick smoke test of the running server if desired:

```bash
uvicorn app.main:app --reload
# In another terminal:
curl -s http://localhost:8000/api/skills | python3 -m json.tool
curl -s -X POST http://localhost:8000/api/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"list files"}' | python3 -m json.tool
```
