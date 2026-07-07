"""MongoDB-backed repository implementations.

These classes mirror the interface of their JSON-file counterparts and are
used when MONGODB_URI is set in the application config.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from pymongo.database import Database

from app.models import RunContext, ScheduledTask, Team, User
from app.runs import _run_matches
from app.settings_store import AppSettings
from app.skills import Skill

logger = logging.getLogger(__name__)

_UNSET = object()


class MongoRunRepository:
    """MongoDB-backed run repository. Same interface as RunRepository."""

    def __init__(self, db: Database) -> None:
        self._col = db["runs"]
        self._recover_in_flight()

    def _recover_in_flight(self) -> None:
        """On startup, mark any in-flight runs as failed (server restarted)."""
        self._col.update_many(
            {"status": {"$in": ["running", "waiting_approval"]}},
            {
                "$set": {
                    "status": "failed",
                    "final_message": "Server restarted.",
                    "pending_command": None,
                }
            },
        )

    def _to_doc(self, ctx: RunContext) -> dict:
        doc = ctx.model_dump(mode="json")
        doc["pending_command"] = None  # transient — never persist
        doc["_id"] = ctx.run_id
        return doc

    def _from_doc(self, doc: dict) -> RunContext:
        d = dict(doc)
        d.pop("_id", None)
        return RunContext.model_validate(d)

    def save(self, ctx: RunContext) -> None:
        self._col.replace_one({"_id": ctx.run_id}, self._to_doc(ctx), upsert=True)

    def list(self, owner_id: str | None = None) -> list[RunContext]:
        query = {"owner_id": owner_id} if owner_id is not None else {}
        return [self._from_doc(d) for d in self._col.find(query)]

    def get(self, run_id: str, owner_id: str | None = None) -> RunContext:
        query: dict = {"_id": run_id}
        if owner_id is not None:
            query["owner_id"] = owner_id
        doc = self._col.find_one(query)
        if doc is None:
            raise KeyError(run_id)
        return self._from_doc(doc)

    def delete(self, run_id: str) -> None:
        result = self._col.delete_one({"_id": run_id})
        if result.deleted_count == 0:
            raise KeyError(run_id)

    def all_as_dict(self) -> dict[str, RunContext]:
        return {d["_id"]: self._from_doc(d) for d in self._col.find()}

    def search(self, query: str) -> list[RunContext]:
        q = query.lower()
        return [ctx for ctx in self.list() if _run_matches(ctx, q)]


class MongoScheduledTaskRepository:
    """MongoDB-backed scheduled task repository."""

    def __init__(self, db: Database) -> None:
        self._col = db["scheduled_tasks"]

    def _to_doc(self, task: ScheduledTask) -> dict:
        doc = task.model_dump(mode="json")
        doc["_id"] = task.task_id
        return doc

    def _from_doc(self, doc: dict) -> ScheduledTask:
        d = dict(doc)
        d.pop("_id", None)
        return ScheduledTask.model_validate(d)

    def save(self, task: ScheduledTask) -> None:
        self._col.replace_one({"_id": task.task_id}, self._to_doc(task), upsert=True)

    def list(self, owner_id: str | None = None) -> list[ScheduledTask]:
        query = {"owner_id": owner_id} if owner_id is not None else {}
        return [self._from_doc(d) for d in self._col.find(query)]

    def get(self, task_id: str, owner_id: str | None = None) -> ScheduledTask:
        query: dict = {"_id": task_id}
        if owner_id is not None:
            query["owner_id"] = owner_id
        doc = self._col.find_one(query)
        if doc is None:
            raise KeyError(task_id)
        return self._from_doc(doc)

    def delete(self, task_id: str) -> None:
        result = self._col.delete_one({"_id": task_id})
        if result.deleted_count == 0:
            raise KeyError(task_id)

    def all_as_dict(self) -> dict[str, ScheduledTask]:
        return {d["_id"]: self._from_doc(d) for d in self._col.find()}


class MongoUserRepository:
    """MongoDB-backed user repository. Same interface as UserRepository."""

    def __init__(self, db: Database) -> None:
        self._col = db["users"]

    def _from_doc(self, doc: dict) -> User:
        d = dict(doc)
        d.pop("_id", None)
        return User.model_validate(d)

    def create(self, username: str, hashed_password: str, role: str) -> User:
        if self._col.find_one({"username": username}):
            raise ValueError(f"User {username!r} already exists")
        user = User(
            user_id=str(uuid.uuid4()),
            username=username,
            hashed_password=hashed_password,
            role=role,
            created_at=datetime.now(timezone.utc),
        )
        doc = user.model_dump(mode="json")
        doc["_id"] = user.user_id
        self._col.insert_one(doc)
        return user

    def get(self, user_id: str) -> User | None:
        doc = self._col.find_one({"_id": user_id})
        return self._from_doc(doc) if doc else None

    def get_by_username(self, username: str) -> User | None:
        doc = self._col.find_one({"username": username})
        return self._from_doc(doc) if doc else None

    def list(self) -> list[User]:
        return [self._from_doc(d) for d in self._col.find()]

    def update(self, user_id: str, **kwargs: object) -> User:
        if not self._col.find_one({"_id": user_id}):
            raise KeyError(f"User {user_id!r} not found")
        self._col.update_one({"_id": user_id}, {"$set": kwargs})
        doc = self._col.find_one({"_id": user_id})
        return self._from_doc(doc)

    def delete(self, user_id: str) -> None:
        result = self._col.delete_one({"_id": user_id})
        if result.deleted_count == 0:
            raise KeyError(f"User {user_id!r} not found")

    def count(self) -> int:
        return self._col.count_documents({})


class MongoSkillRepository:
    """MongoDB-backed skill repository. Same interface as SkillRepository."""

    def __init__(self, db: Database) -> None:
        self._col = db["skills"]

    def _from_doc(self, doc: dict) -> Skill:
        d = dict(doc)
        d.pop("_id", None)
        return Skill.model_validate(d)

    def list(self) -> list[Skill]:
        return [self._from_doc(d) for d in self._col.find().sort("created_at", 1)]

    def get(self, id: str) -> Skill:
        doc = self._col.find_one({"_id": id})
        if doc is None:
            raise KeyError(id)
        return self._from_doc(doc)

    def create(
        self,
        name: str,
        description: str,
        enabled: bool = True,
        policy: str | None = None,
        env: list[str] | None = None,
    ) -> Skill:
        skill = Skill(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            enabled=enabled,
            policy=policy,
            env=env or [],
            created_at=datetime.now(timezone.utc),
        )
        doc = skill.model_dump(mode="json", exclude={"source"})
        doc["_id"] = skill.id
        self._col.insert_one(doc)
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
    ) -> Skill:
        if not self._col.find_one({"_id": id}):
            raise KeyError(id)
        updates: dict = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if enabled is not None:
            updates["enabled"] = enabled
        if policy is not _UNSET:
            updates["policy"] = policy
        if env is not _UNSET:
            updates["env"] = env
        if updates:
            self._col.update_one({"_id": id}, {"$set": updates})
        doc = self._col.find_one({"_id": id})
        return self._from_doc(doc)

    def delete(self, id: str) -> None:
        result = self._col.delete_one({"_id": id})
        if result.deleted_count == 0:
            raise KeyError(id)


class MongoAppSettingsRepository:
    """MongoDB-backed app settings repository. Same interface as AppSettingsRepository."""

    _DOC_ID = "app"

    def __init__(self, db: Database) -> None:
        self._col = db["settings"]

    def load(self) -> AppSettings:
        doc = self._col.find_one({"_id": self._DOC_ID})
        if doc is None:
            return AppSettings()
        d = dict(doc)
        d.pop("_id", None)
        try:
            return AppSettings.model_validate(d)
        except Exception as exc:
            logger.warning("Could not load settings from MongoDB (%s) — using defaults", exc)
            return AppSettings()

    def save(self, s: AppSettings) -> None:
        doc = s.model_dump(mode="json")
        doc["_id"] = self._DOC_ID
        self._col.replace_one({"_id": self._DOC_ID}, doc, upsert=True)


class MongoTeamRepository:
    """MongoDB-backed team repository. Same interface as TeamRepository."""

    def __init__(self, db: Database) -> None:
        self._col = db["teams"]

    def _from_doc(self, doc: dict) -> Team:
        d = dict(doc)
        d.pop("_id", None)
        return Team.model_validate(d)

    def _to_doc(self, team: Team) -> dict:
        doc = team.model_dump(mode="json")
        doc["_id"] = team.team_id
        return doc

    def create(
        self,
        name: str,
        skill_ids: list[str] | None = None,
        skill_repo_url: str | None = None,
        skill_repo_branch: str = "main",
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
    ) -> Team:
        if self._col.find_one({"name": name}):
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
        self._col.insert_one(self._to_doc(team))
        return team

    def get(self, team_id: str) -> Team | None:
        doc = self._col.find_one({"_id": team_id})
        return self._from_doc(doc) if doc else None

    def list(self) -> list[Team]:
        return [self._from_doc(d) for d in self._col.find().sort("created_at", 1)]

    def list_by_user(self, user_id: str) -> list[Team]:
        return [
            self._from_doc(d)
            for d in self._col.find({"user_ids": user_id}).sort("created_at", 1)
        ]

    def update(self, team_id: str, **kwargs: object) -> Team:
        kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = self._col.find_one_and_update(
            {"_id": team_id},
            {"$set": kwargs},
            return_document=True,
        )
        if result is None:
            raise KeyError(f"Team {team_id!r} not found")
        return self._from_doc(result)

    def delete(self, team_id: str) -> None:
        result = self._col.delete_one({"_id": team_id})
        if result.deleted_count == 0:
            raise KeyError(f"Team {team_id!r} not found")

    def add_member(self, team_id: str, user_id: str) -> Team:
        result = self._col.find_one_and_update(
            {"_id": team_id},
            {
                "$addToSet": {"user_ids": user_id},
                "$set": {"updated_at": datetime.now(timezone.utc).isoformat()},
            },
            return_document=True,
        )
        if result is None:
            raise KeyError(f"Team {team_id!r} not found")
        return self._from_doc(result)

    def remove_member(self, team_id: str, user_id: str) -> Team:
        result = self._col.find_one_and_update(
            {"_id": team_id},
            {
                "$pull": {"user_ids": user_id},
                "$set": {"updated_at": datetime.now(timezone.utc).isoformat()},
            },
            return_document=True,
        )
        if result is None:
            raise KeyError(f"Team {team_id!r} not found")
        return self._from_doc(result)

