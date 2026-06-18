from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models import Team
from app.skills import RemoteSkillRepository, Skill, SkillRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File-backed repository
# ---------------------------------------------------------------------------


class TeamRepository:
    """JSON-file-backed team repository."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._teams: dict[str, Team] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"teams.json is not valid JSON: {exc}") from exc
        for raw in data:
            team = Team.model_validate(raw)
            self._teams[team.team_id] = team

    def _save(self) -> None:
        rows = [t.model_dump(mode="json") for t in self._teams.values()]
        self._path.write_text(json.dumps(rows, indent=2))

    def create(
        self,
        name: str,
        skill_ids: list[str] | None = None,
        skill_repo_url: str | None = None,
        skill_repo_branch: str = "main",
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
    ) -> Team:
        with self._lock:
            if any(t.name == name for t in self._teams.values()):
                raise ValueError(f"Team {name!r} already exists")
            now = datetime.now(timezone.utc)
            team = Team(
                team_id=str(uuid.uuid4()),
                name=name,
                user_ids=[],
                skill_ids=skill_ids or [],
                skill_repo_url=skill_repo_url,
                skill_repo_branch=skill_repo_branch,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                created_at=now,
                updated_at=now,
            )
            self._teams[team.team_id] = team
            self._save()
            return team

    def get(self, team_id: str) -> Team | None:
        with self._lock:
            return self._teams.get(team_id)

    def list(self) -> list[Team]:
        with self._lock:
            return list(self._teams.values())

    def list_by_user(self, user_id: str) -> list[Team]:
        with self._lock:
            return [t for t in self._teams.values() if user_id in t.user_ids]

    def update(self, team_id: str, **kwargs: object) -> Team:
        with self._lock:
            team = self._teams.get(team_id)
            if team is None:
                raise KeyError(f"Team {team_id!r} not found")
            kwargs["updated_at"] = datetime.now(timezone.utc)
            updated = team.model_copy(update=kwargs)
            self._teams[team_id] = updated
            self._save()
            return updated

    def delete(self, team_id: str) -> None:
        with self._lock:
            if team_id not in self._teams:
                raise KeyError(f"Team {team_id!r} not found")
            del self._teams[team_id]
            self._save()

    def add_member(self, team_id: str, user_id: str) -> Team:
        with self._lock:
            team = self._teams.get(team_id)
            if team is None:
                raise KeyError(f"Team {team_id!r} not found")
            if user_id not in team.user_ids:
                new_ids = list(team.user_ids) + [user_id]
                updated = team.model_copy(
                    update={"user_ids": new_ids, "updated_at": datetime.now(timezone.utc)}
                )
                self._teams[team_id] = updated
                self._save()
                return updated
            return team

    def remove_member(self, team_id: str, user_id: str) -> Team:
        with self._lock:
            team = self._teams.get(team_id)
            if team is None:
                raise KeyError(f"Team {team_id!r} not found")
            new_ids = [uid for uid in team.user_ids if uid != user_id]
            updated = team.model_copy(
                update={"user_ids": new_ids, "updated_at": datetime.now(timezone.utc)}
            )
            self._teams[team_id] = updated
            self._save()
            return updated


# ---------------------------------------------------------------------------
# Skill & LLM resolution helpers
# ---------------------------------------------------------------------------


def resolve_team_skills(
    user_id: str,
    team_repo: TeamRepository,
    skill_repo: SkillRepository,
    global_remote_repo: RemoteSkillRepository | None,
) -> tuple[list[Skill], list[Skill]] | None:
    """Return (local_skills, remote_skills) filtered for the user's teams.

    Returns None when the user is not a member of any team, signalling that
    the caller should fall back to the global (unrestricted) skill set.
    """
    teams = team_repo.list_by_user(user_id)
    if not teams:
        return None  # no team membership → global defaults apply

    # Union of all skill IDs allowed across all teams the user belongs to
    allowed_skill_ids: set[str] = set()
    for team in teams:
        allowed_skill_ids.update(team.skill_ids)

    local_skills = [s for s in skill_repo.list() if s.id in allowed_skill_ids]

    # Collect remote skills from per-team repositories (deduplicate by URL)
    remote_skills: list[Skill] = []
    seen_urls: set[str] = set()
    for team in teams:
        if not team.skill_repo_url:
            continue
        if team.skill_repo_url in seen_urls:
            continue
        seen_urls.add(team.skill_repo_url)
        repo = RemoteSkillRepository(
            team.skill_repo_url, branch=team.skill_repo_branch
        )
        try:
            remote_skills.extend(repo.sync())
        except Exception as exc:
            logger.warning(
                "Could not sync remote skills from team repo %s: %s",
                team.skill_repo_url,
                exc,
            )

    return local_skills, remote_skills


def resolve_team_llm(
    user_id: str,
    team_repo: TeamRepository,
    global_llm_base_url: str,
    global_llm_api_key: str,
) -> tuple[str, str]:
    """Return (llm_base_url, llm_api_key) for the given user.

    If the user belongs to one or more teams that have a configured LLM
    endpoint, the first matching team's settings win.  Falls back to the
    global settings when no team overrides are present.
    """
    teams = team_repo.list_by_user(user_id)
    for team in teams:
        if team.llm_base_url:
            return team.llm_base_url, team.llm_api_key or ""
    return global_llm_base_url, global_llm_api_key
