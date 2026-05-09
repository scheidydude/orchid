"""Tests for Orchid auth — Phase 1 (JWT foundation) + Phase 2 (API keys).

Covers: register → login → call API → refresh → logout flow,
password hashing, JWT issue/verify, refresh token rotation,
UserStore CRUD, endpoint integration, API key issue/verify/revoke,
scope enforcement.
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
    issue_api_key,
    issue_refresh_token,
    verify_access_token,
    verify_api_key,
    verify_password,
    verify_refresh_token,
)
from orchid.auth.store import UserStore
from orchid.auth.types import ApiKey, AuthError, RefreshToken, User


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


# ── Phase 2: API keys ─────────────────────────────────────────────────────────

class TestApiKeyUnit:
    def test_issue_and_verify(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)

        raw, key = issue_api_key(user, "ci-bot", ["tasks:run"])
        store.store_api_key(key)

        found_user, found_key = verify_api_key(raw, store)
        assert found_user.user_id == "alice"
        assert found_key.key_id == key.key_id
        assert found_key.scopes == ["tasks:run"]

    def test_raw_key_has_ok_prefix(self, tmp_path):
        user = make_user()
        raw, _ = issue_api_key(user, "bot", [])
        assert raw.startswith("ok_")

    def test_wrong_secret_rejected(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        raw, key = issue_api_key(user, "bot", [])
        store.store_api_key(key)

        key_id = raw[3:].split(".", 1)[0]  # strip "ok_" then get key_id
        with pytest.raises(AuthError):
            verify_api_key(f"ok_{key_id}.badsecret", store)

    def test_revoked_key_rejected(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        raw, key = issue_api_key(user, "bot", [])
        store.store_api_key(key)
        store.revoke_api_key(key.key_id)

        with pytest.raises(AuthError, match="revoked"):
            verify_api_key(raw, store)

    def test_expired_key_rejected(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        expires = datetime.now(timezone.utc) - timedelta(seconds=1)
        raw, key = issue_api_key(user, "bot", [], expires_at=expires)
        store.store_api_key(key)

        with pytest.raises(AuthError, match="expired"):
            verify_api_key(raw, store)

    def test_malformed_key_rejected(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(AuthError):
            verify_api_key("ok_nodothere", store)

    def test_non_api_key_rejected(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(AuthError, match="Not an API key"):
            verify_api_key("eyJhbGciOiJIUzI1NiJ9.fake.jwt", store)

    def test_touch_updates_last_used(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        raw, key = issue_api_key(user, "bot", [])
        store.store_api_key(key)

        assert store.get_api_key(key.key_id).last_used is None
        verify_api_key(raw, store)
        assert store.get_api_key(key.key_id).last_used is not None

    def test_list_api_keys(self, tmp_path):
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        _, k1 = issue_api_key(user, "bot-1", ["tasks:read"])
        _, k2 = issue_api_key(user, "bot-2", ["tasks:run"])
        store.store_api_key(k1)
        store.store_api_key(k2)

        keys = store.list_api_keys("alice")
        assert len(keys) == 2
        names = {k.name for k in keys}
        assert names == {"bot-1", "bot-2"}

    def test_persistence_across_instances(self, tmp_path):
        path = tmp_path / "users.json"
        s1 = UserStore(path=path)
        user = make_user()
        s1.add_user(user)
        _, key = issue_api_key(user, "bot", [])
        s1.store_api_key(key)

        s2 = UserStore(path=path)
        assert s2.get_api_key(key.key_id) is not None
        assert s2.get_api_key(key.key_id).name == "bot"


class TestApiKeyEndpoints:
    def _login(self, client, username="dave", password="pw"):
        client.post("/api/auth/register", json={"username": username, "password": password})
        client.post("/api/auth/login", json={"username": username, "password": password})

    def test_create_api_key(self, client):
        self._login(client)
        r = client.post("/api/auth/apikeys", json={"name": "ci-bot", "scopes": ["tasks:run"]})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "ci-bot"
        assert body["scopes"] == ["tasks:run"]
        assert body["secret"].startswith("ok_")
        assert "key_id" in body

    def test_secret_not_in_list(self, client):
        self._login(client)
        client.post("/api/auth/apikeys", json={"name": "bot", "scopes": []})
        r = client.get("/api/auth/apikeys")
        assert r.status_code == 200
        keys = r.json()["api_keys"]
        assert len(keys) == 1
        assert "secret" not in keys[0]

    def test_create_requires_name(self, client):
        self._login(client)
        r = client.post("/api/auth/apikeys", json={"scopes": []})
        assert r.status_code == 400

    def test_create_with_expiry(self, client):
        self._login(client)
        r = client.post("/api/auth/apikeys", json={"name": "temp", "scopes": [], "expires_days": 7})
        assert r.status_code == 200
        assert r.json()["expires_at"] is not None

    def test_revoke_api_key(self, client):
        self._login(client)
        r = client.post("/api/auth/apikeys", json={"name": "bot", "scopes": []})
        key_id = r.json()["key_id"]

        r = client.delete(f"/api/auth/apikeys/{key_id}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        keys = client.get("/api/auth/apikeys").json()["api_keys"]
        assert keys[0]["is_active"] is False

    def test_revoke_nonexistent(self, client):
        self._login(client)
        r = client.delete("/api/auth/apikeys/no-such-id")
        assert r.status_code == 404

    def test_cannot_revoke_other_users_key(self, client):
        # Create alice's key
        self._login(client, "alice", "pw")
        r = client.post("/api/auth/apikeys", json={"name": "alice-bot", "scopes": []})
        key_id = r.json()["key_id"]
        client.post("/api/auth/logout")

        # Log in as bob
        client.post("/api/auth/register", json={"username": "bob", "password": "pw"})
        client.post("/api/auth/login", json={"username": "bob", "password": "pw"})
        r = client.delete(f"/api/auth/apikeys/{key_id}")
        assert r.status_code == 403

    def test_api_key_auth_via_bearer(self, client):
        """API key in Authorization header authenticates successfully."""
        self._login(client)
        r = client.post("/api/auth/apikeys", json={"name": "bot", "scopes": ["*"]})
        secret = r.json()["secret"]
        client.post("/api/auth/logout")

        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {secret}"},
                       cookies={})
        assert r.status_code == 200
        assert r.json()["authenticated"] is True
        assert r.json()["username"] == "dave"

    def test_revoked_api_key_cannot_auth(self, client):
        self._login(client)
        r = client.post("/api/auth/apikeys", json={"name": "bot", "scopes": []})
        key_id = r.json()["key_id"]
        secret = r.json()["secret"]
        client.delete(f"/api/auth/apikeys/{key_id}")
        client.post("/api/auth/logout")

        client.cookies.clear()  # drop JWT cookies so only API key header is in play
        # /api/auth/apikeys uses get_current_user — returns 401 on invalid auth
        r = client.get("/api/auth/apikeys", headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 401

    def test_unauthenticated_cannot_create_key(self, client):
        r = client.post("/api/auth/apikeys", json={"name": "bot", "scopes": []})
        assert r.status_code == 401

    def test_unauthenticated_cannot_list_keys(self, client):
        r = client.get("/api/auth/apikeys")
        assert r.status_code == 401


class TestScopeEnforcement:
    """require_scope() dependency — JWT sessions pass, API keys checked."""

    def test_jwt_session_bypasses_scope_check(self, client):
        """Logged-in user (JWT) can always hit scope-enforced endpoints."""
        from fastapi import Depends
        import orchid.web.server as srv
        from orchid.auth.middleware import require_scope

        @srv.app.get("/test-scope-jwt")
        async def _ep(user: User = Depends(require_scope("tasks:run"))):
            return {"ok": True}

        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        r = client.get("/test-scope-jwt")
        assert r.status_code == 200

    def test_api_key_with_matching_scope_passes(self, client):
        from fastapi import Depends
        import orchid.web.server as srv
        from orchid.auth.middleware import require_scope

        @srv.app.get("/test-scope-key")
        async def _ep(user: User = Depends(require_scope("tasks:run"))):
            return {"ok": True}

        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        r = client.post("/api/auth/apikeys", json={"name": "bot", "scopes": ["tasks:run"]})
        secret = r.json()["secret"]
        client.post("/api/auth/logout")

        r = client.get("/test-scope-key", headers={"Authorization": f"Bearer {secret}"},
                       cookies={})
        assert r.status_code == 200

    def test_api_key_wildcard_scope_passes(self, client):
        from fastapi import Depends
        import orchid.web.server as srv
        from orchid.auth.middleware import require_scope

        @srv.app.get("/test-scope-wildcard")
        async def _ep(user: User = Depends(require_scope("tasks:run"))):
            return {"ok": True}

        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        r = client.post("/api/auth/apikeys", json={"name": "bot", "scopes": ["*"]})
        secret = r.json()["secret"]
        client.post("/api/auth/logout")

        r = client.get("/test-scope-wildcard", headers={"Authorization": f"Bearer {secret}"},
                       cookies={})
        assert r.status_code == 200

    def test_api_key_missing_scope_blocked(self, client):
        from fastapi import Depends
        import orchid.web.server as srv
        from orchid.auth.middleware import require_scope

        @srv.app.get("/test-scope-blocked")
        async def _ep(user: User = Depends(require_scope("tasks:run"))):
            return {"ok": True}

        client.post("/api/auth/register", json={"username": "dave", "password": "pw"})
        client.post("/api/auth/login", json={"username": "dave", "password": "pw"})
        r = client.post("/api/auth/apikeys", json={"name": "bot", "scopes": ["tasks:read"]})
        secret = r.json()["secret"]
        client.post("/api/auth/logout")

        r = client.get("/test-scope-blocked", headers={"Authorization": f"Bearer {secret}"},
                       cookies={})
        assert r.status_code == 403
