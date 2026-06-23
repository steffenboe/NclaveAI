from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Skill(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool = True
    policy: str | None = None   # rego rule bodies; None = no OPA authorization
    env: list[str] = []         # env var names forwarded to subprocess at execution time
    team_id: str | None = None  # assigned to a team (None = global, visible to everyone)
    created_at: datetime
    source: str = "local"       # "local" | "remote" — not persisted in skills.json


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
            json.dumps(
                [s.model_dump(mode="json", exclude={"source"}) for s in self._skills],
                indent=2,
            )
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

    def create(self, name: str, description: str, enabled: bool = True, policy: str | None = None, env: list[str] | None = None, team_id: str | None = None) -> Skill:
        skill = Skill(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            enabled=enabled,
            policy=policy,
            env=env or [],
            team_id=team_id,
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
        env: object = _UNSET,
        team_id: object = _UNSET,
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
        if env is not _UNSET:
            updates["env"] = env
        if team_id is not _UNSET:      # None is a valid value — removes team assignment
            updates["team_id"] = team_id
        updated = self._skills[idx].model_copy(update=updates)
        self._skills[idx] = updated
        self._save()
        return updated.model_copy()

    def delete(self, id: str) -> None:
        idx = self._find_index(id)  # raises KeyError if not found
        del self._skills[idx]
        self._save()


class RemoteSkillRepository:
    def __init__(
        self,
        repo_url: str,
        branch: str = "main",
        cache_dir: Path | None = None,
    ) -> None:
        self._repo_url = repo_url
        self._branch = branch
        if cache_dir is None:
            url_hash = hashlib.md5(repo_url.encode()).hexdigest()[:12]
            cache_dir = Path("/tmp") / f"nclaveai-skills-{url_hash}"
        self._cache_dir = Path(cache_dir)
        self._skills: list[Skill] = []

    def sync(self) -> list[Skill]:
        if (self._cache_dir / ".git").exists():
            cmd = ["git", "-C", str(self._cache_dir), "pull"]
        else:
            cmd = [
                "git", "clone",
                "--depth", "1",
                "--branch", self._branch,
                self._repo_url,
                str(self._cache_dir),
            ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"git command timed out after 60s: {' '.join(cmd)}") from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"git command failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        self._skills = self._parse_yaml_files()
        return list(self._skills)

    def list_skills(self) -> list[Skill]:
        return list(self._skills)

    def _parse_yaml_files(self) -> list[Skill]:
        skills: list[Skill] = []
        for yaml_file in sorted(self._cache_dir.glob("*.yaml")):
            if not yaml_file.is_file():
                continue
            try:
                data = yaml.safe_load(yaml_file.read_text())
                if not isinstance(data, dict):
                    raise ValueError("YAML root must be a mapping")
                name = data.get("name")
                description = data.get("description")
                if not name or not description:
                    raise ValueError("'name' and 'description' are required")
                skill_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{self._repo_url}#{yaml_file.name}",
                    )
                )
                skills.append(
                    Skill(
                        id=skill_id,
                        name=str(name),
                        description=str(description),
                        enabled=bool(data.get("enabled", True)),
                        policy=data.get("policy") or None,
                        env=data.get("env") or [],
                        created_at=datetime.now(timezone.utc),
                        source="remote",
                    )
                )
            except Exception as exc:
                logger.warning("Skipping %s: %s", yaml_file.name, exc)
        return skills
