from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AppSettings(BaseModel):
    skills_repo_url: str | None = None
    skills_repo_branch: str = "main"
    default_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None


class AppSettingsRepository:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> AppSettings:
        if not self._path.exists():
            return AppSettings()
        try:
            data = json.loads(self._path.read_text())
            return AppSettings.model_validate(data)
        except Exception as exc:
            logger.warning("Could not load settings.json (%s) — using defaults", exc)
            return AppSettings()

    def save(self, s: AppSettings) -> None:
        self._path.write_text(json.dumps(s.model_dump(mode="json"), indent=2))
