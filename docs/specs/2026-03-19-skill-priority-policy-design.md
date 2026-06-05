# Skill-Priority Policy Evaluation Design

**Date:** 2026-03-19
**Status:** Draft

---

## Goal

Change the policy evaluation order so that skills act as positive overrides: if a skill's rego permits a command, the command is allowed and the global policy is not consulted. If no skill permits the command, the global policy decides. This replaces the current AND semantics (global AND skill must both allow) with skill-first, global-fallback semantics.

---

## Evaluation Logic

`PolicyEvaluator.evaluate(command)` changes from AND to skill-first-fallback:

1. For each skill interpreter (skills with `policy != None`): evaluate against `{"argv": command.argv}`. If any returns `allow = true` → return `(True, None)` immediately. Global policy is **not consulted**.
2. If no skill allowed the command (no skill interps, or none matched) → evaluate global policy against `{"argv": command.argv}`.
3. If global allows → return `(True, None)`.
4. Return `(False, f"Command {command.argv[0]!r} denied by policy")`.

### Behaviour by case

| Situation | Result |
|---|---|
| Skill allows command | Allowed (global not consulted) |
| Skill exists but does not match command | Falls through to global policy |
| No skills with policies | Global policy decides |
| Global allows, no skill covers | Allowed |
| Global denies, no skill covers | Denied |

### Practical effect with default config

`policies/executor.rego` uses `default allow = false`. With skill-priority evaluation:
- Commands covered by a skill's rego → allowed (skill wins)
- Commands not covered by any skill → denied (global deny-all catches them)
- `executor.rego` reverts to `default allow = false`

---

## Files Changed

| File | Change |
|---|---|
| `app/policy.py` | Reorder `evaluate()`: skill check first, global fallback second |
| `policies/executor.rego` | Revert to `default allow = false` |
| `tests/test_policy.py` | Update tests for new semantics (see below) |

---

## Error Messages

- When a skill allows: `reason = None`
- When global denies (fallback path, including when `_skill_interps` is empty): `reason = f"Command {command.argv[0]!r} denied by policy"`

There is exactly one denial path: global policy denies in step 2/4. The reason string `"No skill policy permits this command"` is **removed** entirely — it is no longer returned in any case.

---

## Test Plan

All changes are in `tests/test_policy.py`. Existing tests whose setup or assertions change:

- `test_evaluator_returns_none_reason_when_allowed` — keep allow-all global + `allow { true }` skill; still passes (skill allows → allowed)
- `test_skill_policy_denies_non_matching_command` — remove explicit `policy_path`; rely on autouse deny-all global; skill allows kubectl, gh not covered → global deny-all → `allowed is False`, `reason == "Command 'gh' denied by policy"`
- `test_no_skill_policy_denies_all` — remove explicit `policy_path`; rely on autouse deny-all global; skill has `policy=None` → no skill interps → global deny-all → `allowed is False`, `reason is not None`
- `test_no_skills_denies_all` — remove explicit `policy_path`; rely on autouse deny-all global; no skills → global deny-all → `allowed is False`, `reason is not None`
- `test_global_deny_takes_priority` — rename to `test_skill_overrides_global_deny`; remove explicit `policy_path`; rely on autouse deny-all global; skill rego `allow { input.argv[0] == "kubectl" }` → command `["kubectl", "get", "pods"]` → `allowed is True`, `reason is None` (skill wins despite global deny-all)

New tests:
- `test_global_allows_when_no_skill_covers` — skill rego allows only kubectl; global is allow-all (tmp_path); `gh pr list` → no skill allows → falls through to global allow-all → `allowed is True, reason is None`
