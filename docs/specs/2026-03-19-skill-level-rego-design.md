# Skill-Level Rego Policy Design

**Date:** 2026-03-19
**Status:** Draft

---

## Goal

Allow each skill to carry its own OPA/Rego rules that define which commands it authorizes. This makes skills self-describing from a security perspective: the skill's description tells the LLM what the tool does, and the skill's policy tells OPA what the tool is allowed to do.

---

## Design Decisions

### Composition: AND with deny-priority

A command is allowed if and only if:

1. The **global policy** allows it (`POLICY_PATH` rego file), AND
2. At least one **enabled skill's policy** allows it

Deny always takes priority at both layers. If the global policy denies, evaluation stops immediately. If no enabled skill's rego authorizes the command, it is denied even if the global policy would have permitted it.

### Skill rego format: rule bodies, package injected

Users write only the rule bodies — no `package` declaration required. The evaluator prepends `package ops.agent\n` to the user's text verbatim before loading it into the interpreter. The rest of the user's text follows unchanged, so multi-rule snippets and helper rules work correctly:

```rego
# User writes (anything — one rule, many rules, helpers):
allow {
  input.argv[0] == "kubectl"
}

allow {
  input.argv[0] == "helm"
}

# Evaluator loads as (package line prepended, everything else unchanged):
package ops.agent

allow {
  input.argv[0] == "kubectl"
}

allow {
  input.argv[0] == "helm"
}
```

The `input` object is identical to the global policy: `{"argv": [...]}`.

Each skill's rego is loaded into its **own `Interpreter` instance** with `add_module("skill", wrapped_text)`. Because each interpreter is isolated, the module name `"skill"` is the same for all — there are no collisions.

### Empty policy: deny all

A skill with `policy = null` contributes nothing to authorization. It cannot permit any command regardless of the global policy. Skills without a policy are LLM-only: they shape the agent's system prompt but provide no OPA authorization.

Note: a skill with `policy = null` and a skill with no enabled skills in scope both result in the same code path — `_skill_interps` is empty — and produce the same denial reason. This is intentional; distinguishing the two cases at the reason level adds no value.

---

## Data Model

`Skill` gains one new field:

```python
class Skill(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool = True
    policy: str | None = None   # NEW — rego rule bodies; None = deny all
    created_at: datetime
```

Stored as-is in `skills.json`. `None` is serialized as JSON `null`.

---

## `create()` signature

`SkillRepository.create()` gains `policy: str | None = None`. The value is passed directly to the `Skill` constructor — no sentinel needed since `None` here unambiguously means "no policy":

```python
def create(self, name: str, description: str, enabled: bool = True, policy: str | None = None) -> Skill:
    skill = Skill(id=..., name=name, description=description, enabled=enabled, policy=policy, created_at=...)
    ...
```

---

## Sentinel Pattern for PATCH

`policy = null` is a valid domain value (means "clear the policy"). This conflicts with the existing `update()` convention where `None` means "not provided / leave unchanged". A sentinel solves this:

```python
_UNSET = object()  # module-level sentinel in app/skills.py

def update(self, id, *, name=None, description=None, enabled=None, policy=_UNSET):
    updates = {}
    if name is not None:
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    if enabled is not None:
        updates["enabled"] = enabled
    if policy is not _UNSET:       # None is a valid value → clears the policy
        updates["policy"] = policy
    ...
```

In the API route, the caller checks `body.model_fields_set` to know whether `policy` was explicitly included in the JSON body, then passes it only if it was:

```python
@app.patch("/api/skills/{skill_id}")
def patch_skill(skill_id: str, body: SkillPatchRequest, request: Request) -> Any:
    kwargs = {}
    if "policy" in body.model_fields_set:
        kwargs["policy"] = body.policy
    skill = request.app.state.skill_repo.update(
        skill_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        **kwargs,
    )
    ...
```

---

## Evaluation Logic

`PolicyEvaluator` is constructed with the global policy path and a pre-filtered list of enabled skills. `_build_workflow` passes `[s for s in skill_repo.list() if s.enabled]`.

The `policy_path` parameter retains its existing default (`None` → falls back to `settings.policy_path`).

Constructor:
```
PolicyEvaluator(policy_path=None, skills: list[Skill] | None = None)
  # skills=None is treated as [] inside __init__ (avoid mutable default)
  → _global_interp  : Interpreter, module name "executor" (unchanged from current code)
  → _skill_interps  : list[Interpreter], one per skill where policy is not None
                      each loaded with add_module("skill", wrapped_text)
```

`evaluate(command) → (allowed: bool, reason: str | None)`:

1. Evaluate global policy against `{"argv": command.argv}`. If denied → return `(False, f"Command {command.argv[0]!r} denied by policy")`. *(Existing denial reason string is preserved.)*
2. If `_skill_interps` is empty (no skills with policy, or no skills at all) → return `(False, "No skill policy permits this command")`.
3. For each skill interpreter: evaluate against `{"argv": command.argv}`. If any returns `allow = true` → return `(True, None)`.
4. Return `(False, "No skill policy permits this command")`.

---

## API Changes

### Request models (in `app/main.py`)

`SkillCreateRequest` gains `policy: str | None = None`.

`SkillPatchRequest` gains `policy: str | None = None`. The route handler uses `model_fields_set` (see sentinel pattern above) to distinguish "not provided" from "explicitly null".

### Endpoint behaviour

`POST /api/skills` — accepts optional `policy` field:
```json
{
  "name": "kubectl",
  "description": "Kubernetes CLI...",
  "policy": "allow {\n  input.argv[0] == \"kubectl\"\n}"
}
```

`PATCH /api/skills/{id}` — accepts optional `policy` field; `null` clears the policy:
```json
{ "policy": null }
```

`GET /api/skills` and `GET /api/skills/{id}` — return `policy` field (may be `null`).

---

## UI Changes

### Skill form

The add/edit skill form gains a third field — **Policy (Rego rules)** — below the description textarea:

- Monospace `<textarea>`
- Optional — may be left empty
- Placeholder:
  ```
  allow {
    input.argv[0] == "kubectl"
  }
  ```
- Helper text below the field: _"Leave empty to disable OPA authorization for this skill."_

### Skill card

Each skill card shows a small inline badge:

- **"policy set"** (green tint) — skill has rego defined
- **"no policy"** (dim/grey) — skill has no rego; cannot authorize commands

---

## Files Changed

| File | Change |
|---|---|
| `app/skills.py` | Add `policy: str \| None = None` to `Skill`; add `policy` param to `create()`; add `policy` + sentinel to `update()` |
| `app/policy.py` | Accept `skills: list[Skill]` param; pre-load per-skill interpreters; updated `evaluate()` |
| `app/main.py` | Pass filtered enabled skills to `PolicyEvaluator` in `_build_workflow`; add `policy` field to `SkillCreateRequest` and `SkillPatchRequest`; use `model_fields_set` in `patch_skill` route |
| `app/static/index.html` | Policy textarea in skill form; policy badge on skill cards |
| `tests/test_skills.py` | Cover `policy` field in repository and API tests, including clearing policy to `null` |
| `tests/test_policy.py` | Skill-level policy tests (see below) |

**Not changed:** `planner.py`, `config.py`, `workflow.py`, `executor.py`

---

## Test Plan

### `tests/test_policy.py`

New tests are **appended to the existing `tests/test_policy.py`** file (not a new file). They therefore inherit the existing `autouse` monkeypatch fixture that sets `settings.policy_path = _REGO_PATH` (pointing at `policies/executor.rego`, which has `default allow = false`).

Tests that need a custom global policy (e.g., allow-all) write a temporary `.rego` file to `tmp_path` and pass it as `policy_path` to `PolicyEvaluator` — same pattern as the existing `test_evaluator_returns_none_reason_when_allowed`.

`test_global_deny_takes_priority` does **not** need a custom policy path — it relies on the autouse fixture, which already points at the deny-all `executor.rego`.

`test_policy_path_defaults_to_settings` also relies on the autouse fixture (which sets `settings.policy_path` to a valid path), so calling `PolicyEvaluator()` with no args uses that path.

- `test_skill_policy_allows_matching_command` — global: allow-all (tmp); skill rego: `allow { input.argv[0] == "kubectl" }`; command `["kubectl", "get", "pods"]` → allowed
- `test_skill_policy_denies_non_matching_command` — global: allow-all (tmp); skill rego: `allow { input.argv[0] == "kubectl" }`; command `["gh", "pr", "list"]` → denied ("No skill policy permits this command")
- `test_no_skill_policy_denies_all` — global: allow-all (tmp); one enabled skill with `policy=None` → command denied
- `test_no_skills_denies_all` — global: allow-all (tmp); no skills passed → command denied
- `test_multi_skill_or_semantics` — global: allow-all (tmp); skill A allows `kubectl`, skill B allows `gh`; command `["gh", "pr", "list"]` → allowed (skill B matches)
- `test_global_deny_takes_priority` — global: deny-all (autouse `executor.rego`); skill rego: `allow { input.argv[0] == "kubectl" }`; command `["kubectl", "get", "pods"]` → denied by global policy
- `test_policy_path_defaults_to_settings` — `PolicyEvaluator()` with no `policy_path` uses `settings.policy_path` (existing behaviour preserved; autouse fixture ensures path is valid)

### `tests/test_skills.py`

**Repository:**
- `test_create_with_policy` — `repo.create(name=..., description=..., policy="allow { true }")` → skill.policy set correctly
- `test_create_without_policy_defaults_to_none` — default `policy=None`
- `test_update_policy` — set policy on a skill that had none
- `test_clear_policy_to_none` — update with `policy=None` explicitly clears it
- `test_policy_round_trip` — create with policy, reload from file, verify policy preserved

**API:**
- `test_api_create_skill_with_policy` — POST with `policy` field → 201, policy returned
- `test_api_patch_skill_policy` — PATCH sets policy on existing skill
- `test_api_patch_clears_policy_to_null` — PATCH with `{"policy": null}` → policy becomes null
- `test_api_patch_omitting_policy_leaves_it_unchanged` — PATCH with no `policy` key → policy unchanged
- `test_api_skill_policy_returned_in_list` — GET `/api/skills` includes `policy` field
