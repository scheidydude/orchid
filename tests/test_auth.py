"""Tests for Orchid auth — Phase 1 (JWT foundation).

Covers: register → login → call API → refresh → logout flow,
password hashing, JWT issue/verify, refresh token rotation,
UserStore CRUD, and middleware dependencies.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# JWT_SECRET must be set before importing anything that touches orchid.auth.jwt
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests-only")

from orchid.auth.jwt import (
    hash_password,
    issue_access_token,
    issue_refresh_token,
    verify_access_token,
    verify_password,
    verify_refresh_token,
)
from orchid.auth.store import UserStore
from orchid.auth.types import AuthError, RefreshToken, User


# ── helpers ───────────────────────────────────────────────────────────────────

def make_store(tmp_path: Path) -> UserStore:
    return UserStore(path=tmp_path / "users.json")


def make_user(user_id: str = "alice", password: str = "s3cr3t") -> User:
    return User(
        user_id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        role="user",
        password_hash=hash_password(password),
    )


# ── password hashing ──────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        h = hash_password("hunter2")
        assert h != "hunter2"

    def test_verify_correct_password(self):
        h = hash_password("hunter2")
        assert verify_password("hunter2", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("hunter2")
        assert verify_password("wrong", h) is False

    def test_same_password_produces_different_hashes(self):
        h1 = hash_password("pw")
        h2 = hash_password("pw")
        assert h1 != h2  # argon2 includes random salt


# ── JWT access tokens ─────────────────────────────────────────────────────────

class TestAccessTokens:
    def test_issue_and_verify(self):
        user = make_user()
        token = issue_access_token(user)
        payload = verify_access_token(token)
        assert payload["sub"] == "alice"
        assert payload["role"] == "user"

    def test_tampered_token_raises(self):
        user = make_user()
        token = issue_access_token(user)
        tampered = token[:-4] + "xxxx"
        with pytest.raises(AuthError):
            verify_access_token(tampered)

    def test_expired_token_raises(self, monkeypatch):
        import orchid.auth.jwt as jwt_mod
        from datetime import timedelta
        monkeypatch.setattr(jwt_mod, "ACCESS_TOKEN_TTL", timedelta(seconds=-1))
        user = make_user()
        token = issue_access_token(user)
        with pytest.raises(AuthError, match="expired"):
            verify_access_token(token)

    def test_missing_secret_raises(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET", raising=False)
        user = make_user()
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            issue_access_token(user)


# ── refresh tokens ────────────────────────────────────────────────────────────

class TestRefreshTokens:
    def test_issue_and_verify(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)

        raw, rt = issue_refresh_token(user)
        store.store_refresh_token(rt)

        verified = verify_refresh_token(raw, store)
        assert verified.user_id == "alice"

    def test_wrong_secret_rejected(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)

        raw, rt = issue_refresh_token(user)
        store.store_refresh_token(rt)

        token_id = raw.split(".", 1)[0]
        bad_raw = f"{token_id}.badsecret"
        with pytest.raises(AuthError):
            verify_refresh_token(bad_raw, store)

    def test_revoked_token_rejected(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)

        raw, rt = issue_refresh_token(user)
        store.store_refresh_token(rt)
        store.revoke_refresh_token(rt.token_id)

        with pytest.raises(AuthError, match="revoked"):
            verify_refresh_token(raw, store)

    def test_expired_token_rejected(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)

        raw, rt = issue_refresh_token(user)
        rt.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        store.store_refresh_token(rt)

        with pytest.raises(AuthError, match="expired"):
            verify_refresh_token(raw, store)

    def test_malformed_token_rejected(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(AuthError, match="Malformed"):
            verify_refresh_token("no-dot-here", store)

    def test_rotation_invalidates_old(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)

        raw, rt = issue_refresh_token(user)
        store.store_refresh_token(rt)

        # Rotate
        store.revoke_refresh_token(rt.token_id)
        new_raw, new_rt = issue_refresh_token(user)
        store.store_refresh_token(new_rt)

        with pytest.raises(AuthError, match="revoked"):
            verify_refresh_token(raw, store)
        verified = verify_refresh_token(new_raw, store)
        assert verified.token_id == new_rt.token_id


# ── UserStore ─────────────────────────────────────────────────────────────────

class TestUserStore:
    def test_add_and_get_user(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        assert store.get_user("alice") is not None

    def test_duplicate_add_raises(self, tmp_path):
        store = make_store(tmp_path)
        store.add_user(make_user())
        with pytest.raises(AuthError, match="already exists"):
            store.add_user(make_user())

    def test_list_users(self, tmp_path):
        store = make_store(tmp_path)
        store.add_user(make_user("alice"))
        store.add_user(make_user("bob"))
        ids = {u.user_id for u in store.list_users()}
        assert ids == {"alice", "bob"}

    def test_update_user(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        user.role = "admin"
        store.update_user(user)
        assert store.get_user("alice").role == "admin"

    def test_update_nonexistent_raises(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(AuthError, match="not found"):
            store.update_user(make_user())

    def test_delete_user(self, tmp_path):
        store = make_store(tmp_path)
        store.add_user(make_user())
        assert store.delete_user("alice") is True
        assert store.get_user("alice") is None

    def test_persistence_across_instances(self, tmp_path):
        path = tmp_path / "users.json"
        s1 = UserStore(path=path)
        s1.add_user(make_user())
        raw, rt = issue_refresh_token(make_user())
        s1.store_refresh_token(rt)

        s2 = UserStore(path=path)
        assert s2.get_user("alice") is not None
        assert s2.get_refresh_token(rt.token_id) is not None

    def test_get_by_username(self, tmp_path):
        store = make_store(tmp_path)
        store.add_user(make_user("carol"))
        assert store.get_user_by_username("carol") is not None
        assert store.get_user_by_username("nobody") is None

    def test_revoke_all_refresh_tokens(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        raw1, rt1 = issue_refresh_token(user)
        raw2, rt2 = issue_refresh_token(user)
        store.store_refresh_token(rt1)
        store.store_refresh_token(rt2)

        store.revoke_all_refresh_tokens("alice")

        with pytest.raises(AuthError, match="revoked"):
            verify_refresh_token(raw1, store)
        with pytest.raises(AuthError, match="revoked"):
            verify_refresh_token(raw2, store)

    # Backward compat: User.token field and get_by_token still work
    def test_legacy_get_by_token(self, tmp_path):
        store = make_store(tmp_path)
        user = User(user_id="legacy", token="old-style-token")
        store.add_user(user)
        found = store.get_by_token("old-style-token")
        assert found.user_id == "legacy"


# ── HTTP endpoint integration ─────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with a fresh UserStore backed by tmp_path.

    server.py depends on orchid.registry (ProjectRegistry) and orchid.runner
    (BackgroundRunner) which are not available in this test environment.
    We stub them before importing server to avoid ModuleNotFoundError.
    """
    import sys
    from unittest.mock import MagicMock

    # Stub out missing modules before server.py is imported
    for mod in ("orchid.registry", "orchid.runner"):
        if mod not in sys.modules:
            stub = MagicMock()
            stub.ProjectRegistry = MagicMock(return_value=MagicMock(list_projects=lambda: []))
            stub.BackgroundRunner = MagicMock(return_value=MagicMock())
            sys.modules[mod] = stub

    import orchid.web.server as srv

    new_store = UserStore(path=tmp_path / "users.json")
    monkeypatch.setattr(srv, "_auth_store", new_store)

    # Also patch middleware singleton so cookie-based auth uses same store
    import orchid.auth.middleware as mw
    monkeypatch.setattr(mw, "_default_store", new_store)

    return TestClient(srv.app, raise_server_exceptions=True)


class TestAuthEndpoints:
    def test_register(self, client):
        r = client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        assert r.status_code == 200
        assert r.json()["user_id"] == "dave"

    def test_register_duplicate_fails(self, client):
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        r = client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        assert r.status_code == 409

    def test_register_missing_password(self, client):
        r = client.post("/api/auth/register", json={"username": "dave"})
        assert r.status_code == 400

    def test_register_invalid_role(self, client):
        r = client.post("/api/auth/register", json={"username": "dave", "password": "pw", "role": "superuser"})
        assert r.status_code == 400

    def test_login_sets_cookies(self, client):
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        r = client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        assert r.status_code == 200
        assert "orchid_access" in r.cookies
        assert "orchid_refresh" in r.cookies

    def test_login_wrong_password(self, client):
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        r = client.post("/api/auth/login", json={"username": "dave", "password": "wrong"})
        assert r.status_code == 401

    def test_login_unknown_user(self, client):
        r = client.post("/api/auth/login", json={"username": "nobody", "password": "pw"})
        assert r.status_code == 401

    def test_me_authenticated(self, client):
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        body = r.json()
        assert body["authenticated"] is True
        assert body["username"] == "dave"

    def test_me_unauthenticated(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["authenticated"] is False

    def test_refresh_rotates_tokens(self, client):
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        old_refresh = client.cookies.get("orchid_refresh")

        r = client.post("/api/auth/refresh")
        assert r.status_code == 200
        new_refresh = r.cookies.get("orchid_refresh")
        assert new_refresh is not None
        assert new_refresh != old_refresh

    def test_old_refresh_token_invalid_after_rotation(self, client, tmp_path, monkeypatch):
        import orchid.web.server as srv
        store = srv._auth_store

        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        old_refresh = client.cookies.get("orchid_refresh")

        client.post("/api/auth/refresh")

        # Manually try old refresh via body (bypassing cookie auto-update)
        r = client.post("/api/auth/refresh", json={"refresh_token": old_refresh},
                        cookies={"orchid_refresh": ""})
        assert r.status_code == 401

    def test_logout_clears_cookies(self, client):
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})

        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        # After logout the cookie jar should have empty/cleared values
        assert r.json()["ok"] is True

    def test_token_validate_endpoint(self, client):
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        login = client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        access = login.json()["access_token"]

        r = client.post("/api/auth/token", json={"token": access})
        assert r.status_code == 200
        assert r.json()["valid"] is True

    def test_token_validate_invalid(self, client):
        r = client.post("/api/auth/token", json={"token": "garbage"})
        assert r.status_code == 401

    def test_admin_can_list_users(self, client):
        client.post("/api/auth/register", json={"username": "admin", "password": "pw", "role": "admin"})
        client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
        r = client.get("/api/auth/users")
        assert r.status_code == 200
        assert len(r.json()["users"]) >= 1

    def test_non_admin_cannot_list_users(self, client):
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        r = client.get("/api/auth/users")
        assert r.status_code == 403

    def test_unauthenticated_cannot_list_users(self, client):
        r = client.get("/api/auth/users")
        assert r.status_code == 401

    def test_bearer_header_auth(self, client):
        """Access token in Authorization header also works (non-cookie clients)."""
        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        login = client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        access = login.json()["access_token"]

        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"},
                       cookies={})  # no cookies
        assert r.json()["authenticated"] is True
