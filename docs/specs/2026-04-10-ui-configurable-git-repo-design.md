# Design: UI-Configurable Remote Git Skill Repository

**Date:** 2026-04-10  
**Status:** Approved

---

## Overview

Replace the `SKILLS_REPO_URL` / `SKILLS_REPO_BRANCH` env vars with a runtime-configurable setting managed via the UI. The repo URL and branch are persisted to a `settings.json` file and survive server restarts. Saving a new URL immediately triggers a clone/pull so remote skills appear without a manual sync.

---

## Storage — `AppSettingsRepository`

New file: `app/settings_store.py`

```python
class AppSettings(BaseModel):
    skills_repo_url: str | None = None
    skills_repo_branch: str = "main"

class AppSettingsRepository:
    def __init__(self, path: Path) -> None: ...
    def load(self) -> AppSettings: ...       # reads file; returns defaults if missing/malformed
    def save(self, s: AppSettings) -> None:  # writes file atomically
```

Backed by `settings.json` (default `./settings.json`, configurable via `SETTINGS_FILE` env var in `config.py`).

File format — flat JSON object:
```json
{"skills_repo_url": "https://github.com/org/skills-repo", "skills_repo_branch": "main"}
```

`SKILLS_REPO_URL` and `SKILLS_REPO_BRANCH` are **removed** from `config.py`. The env vars are no longer read.

---

## Backend Changes

### Startup (`app/main.py` `lifespan`)

1. Instantiate `AppSettingsRepository(settings.settings_file)`, store on `app.state.app_settings_repo`
2. Load `AppSettings` via `.load()`; use `app_settings.skills_repo_url` to decide whether to init `RemoteSkillRepository` (same logic as before, just different source)

### `GET /api/settings`

`SettingsResponse` gains:
```python
skills_repo_url: str | None
skills_repo_branch: str
```

### `PUT /api/settings`

`SettingsPatchRequest` gains:
```python
skills_repo_url: str | None = _UNSET   # sentinel so "not provided" ≠ "set to None"
skills_repo_branch: str | None = None
```

When `skills_repo_url` is provided:
1. Update `AppSettings` and call `AppSettingsRepository.save()`
2. If URL is non-null: create/replace `RemoteSkillRepository`, call `sync()`
   - On success: `app.state.remote_skill_repo = new_repo`
   - On failure: return `503 {"detail": "<git error>"}` (URL is saved; user can retry via sync button)
3. If URL is null: set `app.state.remote_skill_repo = None` (remote skills disappear)

---

## Frontend UI

New **"Remote skill repository"** section in `SkillsModal.jsx`, placed between the LLM settings and the skills list:

```
── Remote skill repository ──────────────────────
Repository URL    https://github.com/org/repo
Branch            main
[ Save repo settings ]
──────────────────────────────────────────────────
```

- Fields pre-filled from `GET /api/settings` on modal open
- "Save repo settings" button calls `PUT /api/settings` with `skills_repo_url` + `skills_repo_branch`
- Button shows "Saving…" while in flight
- On success: reload skills list (remote skills appear immediately)
- On error: display error message inline (e.g. "Failed: git clone failed: repository not found")
- Clearing the URL field and saving removes the remote repo

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| `settings.json` missing on startup | Use defaults (`skills_repo_url = null`), no error |
| `settings.json` malformed | Log warning, use defaults |
| Sync fails when saving new URL | Return `503` with git error; URL still persisted |
| URL cleared (set to `null`) | `app.state.remote_skill_repo = None`; remote skills disappear |
| Server restart with saved URL | Reads `settings.json`, inits and syncs remote repo as before |

---

## Files Changed

| File | Change |
|---|---|
| `app/settings_store.py` | New — `AppSettings` model + `AppSettingsRepository` |
| `app/config.py` | Add `settings_file: Path`; remove `skills_repo_url`, `skills_repo_branch` |
| `app/main.py` | Load `AppSettingsRepository` in lifespan; update `PUT`/`GET /api/settings` |
| `tests/test_settings_store.py` | New — unit tests for `AppSettingsRepository` |
| `tests/test_skills.py` | Update fixtures to use new settings shape |
| `frontend/src/components/SkillsModal.jsx` | Add remote repo config section |
| `frontend/src/App.css` | Style new settings section if needed |
