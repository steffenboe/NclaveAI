from __future__ import annotations

import pytest

from app.auth import (
    create_access_token,
    hash_password,
    verify_password,
    verify_token,
)


class TestPasswordHashing:
    def test_verify_correct_password(self):
        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed)

    def test_reject_wrong_password(self):
        hashed = hash_password("secret123")
        assert not verify_password("wrong", hashed)

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"


class TestJWT:
    def test_create_and_verify_token(self):
        token = create_access_token(subject="user-id-1", role="user")
        payload = verify_token(token)
        assert payload["sub"] == "user-id-1"
        assert payload["role"] == "user"

    def test_verify_tampered_token_raises(self):
        token = create_access_token(subject="user-id-1", role="user")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(Exception):
            verify_token(tampered)

    def test_verify_expired_token_raises(self):
        token = create_access_token(subject="user-id-1", role="user", expires_in_seconds=-1)
        with pytest.raises(Exception):
            verify_token(token)
