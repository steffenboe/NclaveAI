import json
from pathlib import Path

import pytest

from app.secrets_store import SecretsStore


@pytest.fixture
def secrets_file(tmp_path):
    return tmp_path / "secrets.json"


@pytest.fixture
def store(secrets_file):
    return SecretsStore(secrets_file)


def test_empty_store_has_no_secrets(store):
    assert store.list_names() == []


def test_set_and_list(store):
    store.set("API_TOKEN", "secret123")
    assert store.list_names() == ["API_TOKEN"]


def test_set_and_get(store):
    store.set("TOKEN", "val")
    assert store.get("TOKEN") == "val"


def test_get_unknown_returns_none(store):
    assert store.get("NOPE") is None


def test_resolve_filters_to_known_secrets(store):
    store.set("A", "val_a")
    store.set("B", "val_b")
    result = store.resolve(["A", "C"])
    assert result == {"A": "val_a"}


def test_resolve_empty_list(store):
    store.set("A", "val_a")
    assert store.resolve([]) == {}


def test_delete_removes_secret(store):
    store.set("TOKEN", "val")
    store.delete("TOKEN")
    assert store.list_names() == []
    assert store.get("TOKEN") is None


def test_delete_unknown_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.delete("NOPE")


def test_persistence_across_instances(secrets_file):
    store1 = SecretsStore(secrets_file)
    store1.set("TOKEN", "persisted_value")

    store2 = SecretsStore(secrets_file)
    assert store2.get("TOKEN") == "persisted_value"


def test_file_permissions(secrets_file):
    store = SecretsStore(secrets_file)
    store.set("X", "y")
    mode = secrets_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_overwrite_existing_secret(store):
    store.set("TOKEN", "old")
    store.set("TOKEN", "new")
    assert store.get("TOKEN") == "new"
    assert store.list_names() == ["TOKEN"]


def test_load_from_existing_file(secrets_file):
    secrets_file.write_text(json.dumps({"PRE_EXISTING": "value"}))
    store = SecretsStore(secrets_file)
    assert store.get("PRE_EXISTING") == "value"


def test_invalid_json_file_logs_warning(secrets_file, caplog):
    secrets_file.write_text("not json")
    store = SecretsStore(secrets_file)
    assert store.list_names() == []
    assert "Failed to load" in caplog.text


def test_non_dict_json_logs_warning(secrets_file, caplog):
    secrets_file.write_text(json.dumps(["a", "b"]))
    store = SecretsStore(secrets_file)
    assert store.list_names() == []
    assert "Failed to load" in caplog.text
