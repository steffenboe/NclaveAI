from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Skill(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool = True
    policy: str | None = None   # rego rule bodies; None = no OPA authorization
    created_at: datetime


_UNSET = object()


class SkillRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._skills: list[Skill] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning(
                "skills.json not found at %s — starting with empty skills list. "
                "Open the Skills tab in the UI to add tools.",
                self._path,
            )
            return
        try:
            data = json.loads(self._path.read_text())
            self._skills = [Skill.model_validate(s) for s in data]
        except json.JSONDecodeError as exc:
            raise ValueError(f"skills.json is not valid JSON: {exc}") from exc

    def _save(self) -> None:
        self._path.write_text(
            json.dumps([s.model_dump(mode="json") for s in self._skills], indent=2)
        )

    def list(self) -> list[Skill]:
        return [s.model_copy() for s in self._skills]

    def get(self, id: str) -> Skill:
        for skill in self._skills:
            if skill.id == id:
                return skill.model_copy()
        raise KeyError(id)

    def _find_index(self, id: str) -> int:
        for i, skill in enumerate(self._skills):
            if skill.id == id:
                return i
        raise KeyError(id)

    def create(self, name: str, description: str, enabled: bool = True, policy: str | None = None) -> Skill:
        skill = Skill(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            enabled=enabled,
            policy=policy,
            created_at=datetime.now(timezone.utc),
        )
        self._skills.append(skill)
        self._save()
        return skill

    def update(
        self,
        id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        policy: object = _UNSET,
    ) -> Skill:
        idx = self._find_index(id)  # raises KeyError if not found
        updates: dict = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if enabled is not None:
            updates["enabled"] = enabled
        if policy is not _UNSET:       # None is a valid value — clears the policy
            updates["policy"] = policy
        updated = self._skills[idx].model_copy(update=updates)
        self._skills[idx] = updated
        self._save()
        return updated.model_copy()

    def delete(self, id: str) -> None:
        idx = self._find_index(id)  # raises KeyError if not found
        del self._skills[idx]
        self._save()
