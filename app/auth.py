from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings
from app.models import User

_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_SECONDS = 8 * 60 * 60  # 8 hours


# ── Password helpers ──────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────────────────


def create_access_token(
    subject: str,
    role: str,
    expires_in_seconds: int = _ACCESS_TOKEN_EXPIRE_SECONDS,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def verify_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc


# ── FastAPI dependencies ──────────────────────────────────────────────────────


def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
) -> User:
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        payload = verify_token(access_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id: str = payload.get("sub", "")
    user_repo = request.app.state.user_repo
    user: User | None = user_repo.get(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# ── API-key authentication ────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)


def get_user_from_api_key(
    request: Request,
    api_key: str | None = Security(_api_key_header),
) -> User:
    """Resolve the caller's identity from an *X-Api-Key* header.

    Raises HTTP 401 when the header is absent or the key is invalid.
    Updates *last_used_at* on every successful authentication.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required (X-Api-Key header)",
        )

    from app.api_keys import ApiKeyRepository, hash_api_key

    api_key_repo: ApiKeyRepository | None = getattr(request.app.state, "api_key_repo", None)
    if api_key_repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key store not available",
        )

    hashed = hash_api_key(api_key)
    stored = api_key_repo.get_by_hash(hashed)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    user_repo = request.app.state.user_repo
    user: User | None = user_repo.get(stored.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with this API key no longer exists",
        )

    # Fire-and-forget timestamp update — non-fatal if it fails
    try:
        api_key_repo.touch(stored.key_id)
    except Exception:
        pass

    return user


def get_current_user_or_api_key(
    request: Request,
    access_token: str | None = Cookie(default=None),
    api_key: str | None = Security(_api_key_header),
) -> User:
    """Accept either a session cookie **or** an X-Api-Key header.

    Tries cookie auth first; falls back to API key auth.
    Raises HTTP 401 if neither credential is present or valid.
    """
    if access_token:
        try:
            payload = verify_token(access_token)
            user_id: str = payload.get("sub", "")
            user = request.app.state.user_repo.get(user_id)
            if user is not None:
                return user
        except ValueError:
            pass  # fall through to API key

    if api_key:
        return get_user_from_api_key(request, api_key)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
