from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models import User

logger = logging.getLogger(__name__)


class UserRepository:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"users.json is not valid JSON: {exc}") from exc
        for raw in data:
            user = User.model_validate(raw)
            self._users[user.user_id] = user

    def _save(self) -> None:
        rows = [u.model_dump(mode="json") for u in self._users.values()]
        self._path.write_text(json.dumps(rows, indent=2))

    def create(self, username: str, hashed_password: str, role: str) -> User:
        with self._lock:
            if any(u.username == username for u in self._users.values()):
                raise ValueError(f"User {username!r} already exists")
            user = User(
                user_id=str(uuid.uuid4()),
                username=username,
                hashed_password=hashed_password,
                role=role,
                created_at=datetime.now(timezone.utc),
            )
            self._users[user.user_id] = user
            self._save()
            return user

    def get(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def get_by_username(self, username: str) -> User | None:
        with self._lock:
            return next(
                (u for u in self._users.values() if u.username == username), None
            )

    def list(self) -> list[User]:
        with self._lock:
            return list(self._users.values())

    def update(self, user_id: str, **kwargs: object) -> User:
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                raise KeyError(f"User {user_id!r} not found")
            updated = user.model_copy(update=kwargs)
            self._users[user_id] = updated
            self._save()
            return updated

    def delete(self, user_id: str) -> None:
        with self._lock:
            if user_id not in self._users:
                raise KeyError(f"User {user_id!r} not found")
            del self._users[user_id]
            self._save()

    def count(self) -> int:
        with self._lock:
            return len(self._users)
