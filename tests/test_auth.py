"""Tests for orchid.auth.types and orchid.auth.store (T260)."""
from pathlib import Path
import pytest
from orchid.auth.types import User, AuthError
from orchid.auth.store import UserStore


def test_user_dataclass_defaults():
    user = User(user_id="u1", token="tok")
    assert user.projects == []
    assert user.api_keys == {}
    assert user.budget_usd == 0.0


def test_userstore_add_and_get_by_token(tmp_path):
    store = UserStore(path=tmp_path / "users.json")
    user = User(user_id="u1", token="secret")
    store.add_user(user)
    found = store.get_by_token("secret")
    assert found.user_id == "u1"


def test_userstore_get_by_id(tmp_path):
    store = UserStore(path=tmp_path / "users.json")
    user = User(user_id="u2", token="tok2")
    store.add_user(user)
    found = store.get_by_id("u2")
    assert found.token == "tok2"


def test_userstore_remove_user(tmp_path):
    store = UserStore(path=tmp_path / "users.json")
    user = User(user_id="u3", token="tok3")
    store.add_user(user)
    store.remove_user("u3")
    with pytest.raises(AuthError):
        store.get_by_id("u3")


def test_userstore_get_by_token_raises_on_invalid(tmp_path):
    store = UserStore(path=tmp_path / "users.json")
    with pytest.raises(AuthError):
        store.get_by_token("no-such-token")
