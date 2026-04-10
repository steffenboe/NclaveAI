# Design: Remote Git Skill Repository

**Date:** 2026-04-10  
**Status:** Approved

---

## Overview

Instead of (or in addition to) maintaining skills in a local `skills.json` file, the server can be pointed at a remote Git repository. When configured, it clones the repo at startup and reads all top-level `.yaml` files as skills. These remote skills are read-only overlays on top of the existing local skills.

---

## Configuration

Two new optional env vars added to `app/config.py` (`Settings`):

| Variable | Default | Description |
|---|---|---|
| `SKILLS_REPO_URL` | `None` | Git clone URL of the remote skills repo (e.g. `https://github.com/org/skills-repo`). If unset, feature is disabled. |
| `SKILLS_REPO_BRANCH` | `"main"` | Branch to clone/pull. |

When `SKILLS_REPO_URL` is unset, behavior is 100% identical to today.

---

## Skill File Format

Each top-level `.yaml` file in the remote repo represents one skill:

```yaml
name: kubectl
description: |
  Use kubectl to discover, inspect and edit Kubernetes resources.
  Pass -o json for structured output. Always check the current context first.
enabled: true        # optional, default: true
policy: |            # optional, default: null
  allow {
    input.argv[0] == "kubectl"
  }
```

- `name` and `description` are required.
- `enabled` defaults to `true` if absent.
- `policy` defaults to `null` if absent.
- Files in subdirectories are ignored.
- Skill IDs are derived deterministically via UUID5 from `(repo_url, filename)` so they are stable across restarts and re-syncs.

---

## Architecture

### New class: `RemoteSkillRepository` (`app/skills.py`)

```
RemoteSkillRepository
├── __init__(repo_url, branch, cache_dir)
├── sync() -> list[Skill]          # clone or pull, then parse .yaml files
└── list_skills() -> list[Skill]   # returns last synced list (in-memory)
```

**`sync()` logic:**
1. If `cache_dir` is not a git repo: `git clone --depth 1 --branch <branch> <url> <cache_dir>`
2. If already cloned: `git -C <cache_dir> pull`
3. Glob `<cache_dir>/*.yaml`, parse each file, return `list[Skill]`
4. Subprocess errors → raise `RuntimeError` with message

`cache_dir` defaults to a stable temp path derived from the repo URL (e.g. `/tmp/llm-opa-agent-skills/<url-hash>`).

Remote skills carry a `source = "remote"` field (added to `Skill` model, not persisted in `skills.json`) to allow the API/UI to distinguish them.

### Startup flow (`app/main.py` `lifespan`)

1. Always instantiate `SkillRepository` (local), as today.
2. If `settings.skills_repo_url` is set:
   - Instantiate `RemoteSkillRepository`, call `sync()`.
   - On failure: log warning, store empty remote list, continue startup.
   - Store on `app.state.remote_skill_repo`.

### Combined skill list

All endpoints that return or consume the full skill list merge both sources:

```
combined = remote_skills + local_skills
```

Remote skills come first (arbitrary choice for determinism). The combined list is what `Planner` and `PolicyEvaluator` receive.

### New API endpoint

`POST /api/skills/sync`

- Calls `remote_skill_repo.sync()`.
- Returns `200 {"skills": [...]}` with the refreshed combined list on success.
- Returns `503 {"detail": "<error message>"}` if git fails.
- Returns `404` if `SKILLS_REPO_URL` is not configured.

### Updated settings endpoint

`GET /api/settings` response gains a `skills_repo_configured: bool` field so the frontend knows whether to show the sync button.

---

## Frontend UI Changes

- Remote skills in the list show a **"remote" badge** (small label/icon).
- **Edit and Delete buttons are hidden** for remote skills (read-only).
- A **"Sync remote skills" button** appears in the skills list header, only when `skills_repo_configured` is `true`.
  - Clicking it calls `POST /api/skills/sync`.
  - Shows a loading state during the request.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| `SKILLS_REPO_URL` not set | Feature entirely disabled; no code path changes |
| `git clone` fails at startup | Log warning; remote skills = `[]`; local skills unaffected; server starts normally |
| `git pull` fails at `/api/skills/sync` | Return `503` with error detail; cached skills unchanged |
| `.yaml` file has parse error or missing required fields | Log warning; skip that file; continue loading others |
| Remote repo has zero `.yaml` files | Empty remote list; no error |

---

## Implementation Approach

**Subprocess `git`** — shell out to the system `git` binary via `subprocess.run`. No new Python dependencies required.

- Clone: `git clone --depth 1 --branch <branch> <url> <cache_dir>`
- Pull: `git -C <cache_dir> pull`

---

## Out of Scope (Future Work)

- Private repo authentication (SSH key, PAT)
- Per-run override of remote skills (they follow the `enabled` field in the YAML)
- Periodic background polling
- Watching the remote for push events (webhooks)
