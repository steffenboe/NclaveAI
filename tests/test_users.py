from __future__ import annotations

import pytest

from app.auth import hash_password
from app.users import UserRepository


@pytest.fixture
def repo(tmp_path):
    return UserRepository(tmp_path / "users.json")


class TestUserRepository:
    def test_create_and_get_by_username(self, repo):
        user = repo.create(username="alice", hashed_password=hash_password("pw"), role="user")
        found = repo.get_by_username("alice")
        assert found is not None
        assert found.user_id == user.user_id

    def test_get_returns_none_for_unknown_id(self, repo):
        assert repo.get("nonexistent-id") is None

    def test_get_by_username_returns_none_for_unknown(self, repo):
        assert repo.get_by_username("nobody") is None

    def test_list_returns_all_users(self, repo):
        repo.create(username="alice", hashed_password=hash_password("pw"), role="user")
        repo.create(username="bob", hashed_password=hash_password("pw"), role="admin")
        assert len(repo.list()) == 2

    def test_delete_user(self, repo):
        user = repo.create(username="alice", hashed_password=hash_password("pw"), role="user")
        repo.delete(user.user_id)
        assert repo.get_by_username("alice") is None

    def test_delete_nonexistent_raises(self, repo):
        with pytest.raises(KeyError):
            repo.delete("nonexistent-id")

    def test_duplicate_username_raises(self, repo):
        repo.create(username="alice", hashed_password=hash_password("pw"), role="user")
        with pytest.raises(ValueError, match="already exists"):
            repo.create(username="alice", hashed_password=hash_password("pw"), role="user")

    def test_count(self, repo):
        assert repo.count() == 0
        repo.create(username="alice", hashed_password=hash_password("pw"), role="user")
        assert repo.count() == 1

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "users.json"
        r1 = UserRepository(path)
        r1.create(username="alice", hashed_password=hash_password("pw"), role="user")

        r2 = UserRepository(path)
        assert r2.get_by_username("alice") is not None

    def test_update_user(self, repo):
        user = repo.create(username="alice", hashed_password=hash_password("pw"), role="user")
        updated = repo.update(user.user_id, role="admin")
        assert updated.role == "admin"
        assert repo.get(user.user_id).role == "admin"
