# Design: Per-Conversation Skill Overrides

**Date:** 2026-03-31
**Status:** Approved

## Context

The agent already supports a global skills system: each skill has an `enabled` boolean that determines whether its OPA policy participates in command evaluation and whether its description is injected into the LLM system prompt.

This design adds the ability to toggle skills on or off on a per-conversation (per-run) basis, overriding the global enabled state for the duration of that run.

## Requirements

- Users can enable or disable any existing skill for an individual conversation
- Toggles can be changed dynamically at any point during the conversation
- Per-conversation toggles fully override the global enabled flag (a globally-disabled skill can be enabled for a conversation, and vice versa)
- Overrides persist with the run record (survive server restarts)
- No new skills can be created at the conversation level — only existing skills can be toggled

## Approach: Skill Override Map on RunContext

A sparse `skill_overrides: dict[str, bool]` field is added to `RunContext`. The key is a skill UUID; the value is the overridden enabled state. An absent key means "use the global enabled flag." This is the minimal representation — only explicitly changed skills appear in the map.

Alternatives considered:
- **Snapshot skills at run start** — full copy of skill objects per run; rejected due to data duplication and inability to reflect globally-added skills mid-conversation.
- **Separate ConversationSkillState entity** — new storage layer with its own CRUD; rejected as over-engineered for a sparse override map.

## Data Model

`app/models.py` — `RunContext` gains one field:

```python
skill_overrides: dict[str, bool] = {}
```

## Policy Evaluation

`PolicyEvaluator` currently receives a pre-filtered list of enabled skills at construction time. With per-conversation overrides, the effective skill set can change mid-run.

Changes:
- `PolicyEvaluator.__init__` receives the **full** list of skills (not pre-filtered), plus each skill's `id` so override resolution is possible.
- `PolicyEvaluator` stores `_skill_interps: list[tuple[str, str, Interpreter]]` where the tuple is `(skill_id, skill_name, interp)`.
- `PolicyEvaluator.evaluate()` gains an optional `skill_overrides: dict[str, bool] | None` parameter.
- Inside `evaluate()`, for each skill interpreter the effective enabled state is resolved as: `skill_overrides.get(skill_id, skill.enabled)`. Only effectively-enabled skills participate in evaluation.
- `AgentWorkflow.run()` passes `ctx.skill_overrides` into every `policy.evaluate()` call.
- `_build_workflow()` in `main.py` passes all skills (not just `s.enabled`) to `PolicyEvaluator`.

## API

### Toggle a skill for a run

```
PATCH /api/agent/runs/{run_id}/skills/{skill_id}
Content-Type: application/json

{ "enabled": true }
```

- 404 if run or skill does not exist
- Updates `ctx.skill_overrides[skill_id]` in-memory

### Get effective skill states for a run

```
GET /api/agent/runs/{run_id}/skills
```

Returns the full skill list with an `effective_enabled` field reflecting the override-resolved state for that run:

```json
[
  {
    "id": "...",
    "name": "kubectl",
    "enabled": true,          // global state
    "effective_enabled": false // conversation override
    ...
  }
]
```

## UI

- The existing global Skills modal (sidebar) is unchanged.
- When a conversation is active, a "Skills" section is rendered in the conversation toolbar/header area.
- It fetches `GET /api/agent/runs/{run_id}/skills` and displays a toggle per skill using `effective_enabled` as the current state.
- Toggling calls `PATCH /api/agent/runs/{run_id}/skills/{skill_id}`.
- Visually similar to the global skills modal but labelled "This conversation" or similar to make the scope clear.

## Testing

- **Unit tests** (`tests/test_policy.py`):
  - Override enables a globally-disabled skill → command allowed
  - Override disables a globally-enabled skill → command blocked
  - No override → falls back to global enabled flag (existing behavior preserved)
- **API tests** (`tests/test_skills.py` or new `tests/test_run_skills.py`):
  - `PATCH` returns 404 for unknown run / unknown skill
  - `PATCH` updates the override and is reflected in subsequent `GET /api/agent/runs/{run_id}/skills`
  - `GET` returns correct `effective_enabled` merging global and override states
- **Integration test** (`tests/test_workflow.py`):
  - Run with globally-enabled skill disabled via override: command that would be allowed by the skill policy is blocked

## Error Handling

- `PATCH` on a completed/failed run is allowed (override stored; has no practical effect since no more commands will run, but is not an error)
- Unknown skill IDs in `skill_overrides` are silently ignored during evaluation (the skill simply doesn't exist to match)
