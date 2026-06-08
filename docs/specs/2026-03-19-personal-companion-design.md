# Personal Developer Companion — Design Spec

**Date:** 2026-03-19
**Branch:** implement-byot

---

## Overview

Transform `NclaveAI` from a Kubernetes-deployed operations agent into a **personal developer companion** that runs locally on the developer's machine.

The agent gains an extensible skill repository: a managed collection of CLI tool descriptors that tell the LLM which tools are available and how to invoke them. The developer interacts through the existing browser UI, manages skills through a new Skills page, and limits what the agent may do via an OPA Rego policy.

The core planner → OPA → executor loop is unchanged.

---

## Goals

- Run locally on the developer's machine, inheriting their environment (PATH, credentials, kubeconfig, etc.)
- Support any CLI tool through a skill descriptor, not just kubectl/helm
- Let the developer manage skills (add, edit, delete, enable/disable) through the browser UI
- Let the developer constrain the agent's capabilities with a standard OPA Rego policy
- Keep the architectural footprint minimal — no new external services or databases

## Non-Goals

- Multi-user or shared deployment
- Tool installation / bootstrapping (developer installs CLI tools themselves)
- Per-skill auth configuration (tools use whatever credentials are already in the environment)

---

## Architecture

The planner-executor-OPA loop is untouched:

```
prompt
  └── Planner.next_action()        ← LLM decides: run command / done / failed
        └── PolicyEvaluator.evaluate()   ← OPA allows or denies
              └── CommandExecutor.run()  ← subprocess executes
                    └── result appended to history → repeat
```

Three changes are layered on top:

1. **Dynamic planner system prompt** — `Planner` receives the `SkillRepository` and injects enabled skill descriptors into the system prompt at request time.
2. **Skill repository** — new `SkillRepository` class backed by a `skills.json` file. Owned by the FastAPI app as a singleton.
3. **Skills UI** — new tab in `index.html` for CRUD management of skills.

---

## Skill Model

```python
class Skill(BaseModel):
    id: str           # UUID, assigned on creation
    name: str         # e.g. "kubectl", "gh", "terraform"
    description: str  # free-form LLM instructions (usage, common flags, caveats)
    enabled: bool = True
    created_at: datetime
```

The `description` field is the core of a skill. It is free-form text the LLM reads to understand what the tool does and how to invoke it. It can include usage examples, flag reference, and any constraints the developer wants the agent to respect.

**Example skill:**
```json
{
  "id": "a1b2c3d4-...",
  "name": "kubectl",
  "description": "Kubernetes CLI. Use to inspect and manage cluster resources.\n\nCommon patterns:\n- kubectl get pods -n <namespace>\n- kubectl logs <pod> -n <namespace>\n- kubectl describe deployment <name> -n <namespace>\n- kubectl rollout restart deployment/<name> -n <namespace>\n\nAlways specify -n <namespace>. Never delete resources unless explicitly asked.",
  "enabled": true,
  "created_at": "2026-03-19T10:00:00Z"
}
```

---

## Skill Repository

**Class:** `app/skills.py` — `SkillRepository`

**Persistence:** JSON file at path configured by `SKILLS_FILE` env var (default: `./skills.json`). Skills are stored in a JSON array in insertion order; `list()` returns them in that order with no additional sorting. Missing file → logs a startup warning and starts with an empty list (so the developer knows why the agent has no tools). Invalid JSON at startup → fatal error with clear message.

**Concurrency:** This is a single-user local tool. Concurrent mutations to the file are not a concern; no file locking is required.

**Interface:**
- `list() -> list[Skill]` — all skills, in JSON insertion order
- `get(id: str) -> Skill` — raises `KeyError` if not found
- `create(name, description, enabled) -> Skill` — assigns UUID + timestamp, appends, persists
- `update(id, *, name=None, description=None, enabled=None) -> Skill` — updates only the supplied fields; `id` and `created_at` are immutable and never modified; raises `KeyError` if not found
- `delete(id) -> None` — removes matching skill, persists; raises `KeyError` if not found

All mutations write the full list back to `skills.json` immediately. Write failures propagate as exceptions (no silent data loss).

**`updated_at` field:** intentionally omitted from the model to keep it minimal. Can be added in a future iteration if edit history becomes useful.

---

## REST API — Skills

New router mounted at `/api/skills`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/skills` | List all skills |
| `GET` | `/api/skills/{id}` | Get a single skill |
| `POST` | `/api/skills` | Create a skill. Body: `{name, description, enabled?}` — `name` and `description` required; `enabled` optional, defaults to `true` |
| `PATCH` | `/api/skills/{id}` | Partial update — any subset of `{name, description, enabled}` |
| `DELETE` | `/api/skills/{id}` | Delete a skill |

`GET/PATCH/DELETE` on unknown `id` returns `404`. All responses are JSON.

The UI populates the edit form from the skills list already held in memory. Single-resource `GET` is available for direct API consumers.

---

## Planner Integration

`Planner.__init__` receives the `SkillRepository`. `next_action()` calls `repo.list()`, filters `enabled=True`, and builds the system prompt at call time using a Python f-string loop over the enabled skills:

**With skills (example with kubectl and gh):**
```
You are an autonomous developer companion agent.

You have access to the following CLI tools:

[kubectl]
Kubernetes CLI. Use to inspect and manage cluster resources. ...

[gh]
GitHub CLI. Use to interact with GitHub repositories, issues, PRs. ...

Rules:
- Always read before writing.
- Only take the minimum action required.
- If the last action's output shows the problem is resolved, return status=done.
- If you have tried 3+ actions without progress, return status=failed.
- Produce the next action as a single argv list using one of these tools.
  No shell expansion, no pipes, no redirection.
- Your rationale field is for the audit log only — be concise.
```

**With no enabled skills:**
```
You are an autonomous developer companion agent.

No CLI tools are currently available. Return status=failed immediately.
```

Each `skill.description` is inserted verbatim (including newlines). Skill content is developer-provided trusted input; prompt injection from the description field is outside the threat model for this personal tool.

---

## UI — Skills Tab

The existing `index.html` gets a second tab alongside the run dashboard: **Skills**.

**Skills tab contents:**
- List of all skills: name, first line of description (truncated), enabled toggle, Edit button, Delete button.
- "Add Skill" button — opens an inline form.
- Form fields: `name` (text input), `description` (textarea), `enabled` (checkbox).
- Edit opens the same form pre-filled with current values.
- All interactions via `fetch()` against `/api/skills` — no page reloads.

The existing run dashboard, history view, and webhook list are unchanged.

---

## Configuration Changes

| Variable | Change | Notes |
|---|---|---|
| `LLM_BASE_URL` | unchanged | |
| `LLM_API_KEY` | unchanged | |
| `LLM_MODEL` | unchanged | |
| `POLICY_PATH` | unchanged | Still required; use allow-all stub for development |
| `MAX_ITERATIONS` | unchanged | |
| `SKILLS_FILE` | **new** | Path to `skills.json`. Default: `./skills.json` |
| `AGENT_ROLES` | **removed** | OPA receives `input.argv`; role scoping replaced by skills + Rego |
| `KUBECONFIG` | **removed** | Inherited from developer's environment |
| `KUBE_NAMESPACE` | **removed** | Inherited from developer's environment |

---

## OPA Policy

No change to the OPA integration. The policy receives:

```rego
input.argv   # the command as a list, e.g. ["kubectl", "get", "pods", "-n", "prod"]
```

Developers write Rego to constrain what the agent may execute. Example: deny writes to production namespaces, deny `kubectl delete`, allow only specific tools.

The `input.roles` field is removed from the input (was populated by `AGENT_ROLES`).

---

## Error Handling

| Scenario | Behavior |
|---|---|
| `skills.json` missing at startup | Logs a warning; starts with empty skills list |
| `skills.json` corrupt (invalid JSON) | Fatal error at startup with clear message |
| Skill `id` not found | Repository raises `KeyError`; API returns 404 |
| Write failure (disk full, permissions) | Exception propagates; API returns 500 |
| No enabled skills | Planner system prompt states no tools available; LLM returns `status=failed` |

---

## Testing

| Test file | Coverage |
|---|---|
| `tests/test_skills.py` | `SkillRepository` CRUD, toggle, JSON round-trip, missing file (warning logged), corrupt file; API layer: all endpoints including `GET /api/skills/{id}` 200 and 404, `PATCH` partial update, `DELETE` 404 |
| `tests/test_planner.py` | System prompt includes skill descriptors when skills present; no-skills fallback prompt when empty |
| `tests/test_executor.py` | Unchanged |
| `tests/test_policy.py` | Unchanged |
| `tests/test_workflow.py` | Unchanged |

---

## New Files

- `app/skills.py` — `Skill` model + `SkillRepository`
- `tests/test_skills.py` — skill repository tests

## Modified Files

- `app/config.py` — add `SKILLS_FILE`, remove `AGENT_ROLES` / `KUBECONFIG` / `KUBE_NAMESPACE`
- `app/main.py` — instantiate `SkillRepository`, pass to `Planner`, mount skills router
- `app/planner.py` — accept `SkillRepository`, build dynamic system prompt
- `app/static/index.html` — add Skills tab
- `.env.example` — reflect config changes
- `README.md` — replace Kubernetes deployment content with local install instructions

The updated README install section must cover:
1. Prerequisites: Python 3.12+, OPA binary (or `regopy`), and whichever CLI tools the developer wants to use as skills
2. Install: `pip install -e ".[dev]"`
3. Configure: `cp .env.example .env` — set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `POLICY_PATH`; optionally set `SKILLS_FILE`
4. Run: `uvicorn app.main:app --reload --port 8081`, then open `http://localhost:8081`
5. First steps: open the Skills tab, add a skill for each CLI tool you want the agent to use
