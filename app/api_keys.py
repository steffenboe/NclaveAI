from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models import ApiKey

_KEY_PREFIX = "ncl_"
_KEY_PREFIX_DISPLAY_LEN = 12  # "ncl_" + 8 hex chars shown in listings


def _get_hmac_secret() -> bytes:
    """Derive an HMAC secret from the app's JWT secret.

    Importing lazily to avoid circular imports at module load time.
    Using a distinct context string ensures the derived key is separate
    from the JWT signing key even though they share the same root secret.
    """
    from app.config import settings
    return hmac.new(
        settings.jwt_secret.encode(),
        b"nclave:api-keys:v1",
        hashlib.sha256,
    ).digest()


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns (raw_key, key_prefix, hashed_key).
    The raw_key is only returned here and must be shown to the user once.
    The hashed_key is HMAC-SHA256 keyed with a server-side secret so the
    hash is useless to an attacker who only has the database.
    """
    raw = _KEY_PREFIX + secrets.token_hex(32)
    key_prefix = raw[:_KEY_PREFIX_DISPLAY_LEN]
    hashed = hash_api_key(raw)
    return raw, key_prefix, hashed


def hash_api_key(raw_key: str) -> str:
    """HMAC-SHA256 of *raw_key* using a server-side derived secret.

    Deterministic (fast O(1) lookup), but requires the server secret —
    a leaked hash database cannot be used to verify keys offline.
    """
    return hmac.new(_get_hmac_secret(), raw_key.encode(), hashlib.sha256).hexdigest()


class ApiKeyRepository:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._keys: dict[str, ApiKey] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"api_keys.json is not valid JSON: {exc}") from exc
        for raw in data:
            key = ApiKey.model_validate(raw)
            self._keys[key.key_id] = key

    def _save(self) -> None:
        rows = [k.model_dump(mode="json") for k in self._keys.values()]
        self._path.write_text(json.dumps(rows, indent=2))

    def create(self, user_id: str, name: str, hashed_key: str, key_prefix: str) -> ApiKey:
        with self._lock:
            key = ApiKey(
                key_id=str(uuid.uuid4()),
                user_id=user_id,
                name=name,
                key_prefix=key_prefix,
                hashed_key=hashed_key,
                created_at=datetime.now(timezone.utc),
            )
            self._keys[key.key_id] = key
            self._save()
            return key

    def list_by_user(self, user_id: str) -> list[ApiKey]:
        with self._lock:
            return [k for k in self._keys.values() if k.user_id == user_id]

    def get_by_hash(self, hashed_key: str) -> ApiKey | None:
        with self._lock:
            return next((k for k in self._keys.values() if k.hashed_key == hashed_key), None)

    def touch(self, key_id: str) -> None:
        with self._lock:
            if key_id in self._keys:
                self._keys[key_id] = self._keys[key_id].model_copy(
                    update={"last_used_at": datetime.now(timezone.utc)}
                )
                self._save()

    def delete(self, key_id: str, user_id: str | None = None) -> None:
        """Delete an API key.  If *user_id* is given the key must belong to that
        user (used for self-service deletion).  Pass ``user_id=None`` for
        admin-scoped deletions.
        """
        with self._lock:
            key = self._keys.get(key_id)
            if key is None or (user_id is not None and key.user_id != user_id):
                raise KeyError(key_id)
            del self._keys[key_id]
            self._save()
