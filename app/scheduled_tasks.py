from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.models import ScheduledTask

logger = logging.getLogger(__name__)


class ScheduledTaskRepository:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._tasks: dict[str, ScheduledTask] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning(
                "scheduled_tasks.json not found at %s - starting with empty task list.",
                self._path,
            )
            return
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"scheduled_tasks.json is not valid JSON: {exc}") from exc

        for raw in data:
            task = ScheduledTask.model_validate(raw)
            self._tasks[task.task_id] = task

    def _save_all(self) -> None:
        rows = [task.model_dump(mode="json") for task in self._tasks.values()]
        self._path.write_text(json.dumps(rows, indent=2))

    def save(self, task: ScheduledTask) -> None:
        with self._lock:
            self._tasks[task.task_id] = task
            self._save_all()

    def list(self, owner_id: str | None = None) -> list[ScheduledTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        if owner_id is not None:
            tasks = [task for task in tasks if task.owner_id == owner_id]
        return [task.model_copy() for task in tasks]

    def get(self, task_id: str, owner_id: str | None = None) -> ScheduledTask:
        with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            task = self._tasks[task_id]
        if owner_id is not None and task.owner_id != owner_id:
            raise KeyError(task_id)
        return task.model_copy()

    def delete(self, task_id: str) -> None:
        with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            del self._tasks[task_id]
            self._save_all()

    def all_as_dict(self) -> dict[str, ScheduledTask]:
        with self._lock:
            return {task_id: task.model_copy() for task_id, task in self._tasks.items()}
