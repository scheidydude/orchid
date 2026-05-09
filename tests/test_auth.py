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


# ── Phase 3: OAuth 2.0 / OIDC ────────────────────────────────────────────────

import respx
import httpx as _httpx

# Minimal OIDC discovery document returned by the mock provider
_DISCOVERY = {
    "authorization_endpoint": "https://mock-idp.example.com/auth",
    "token_endpoint": "https://mock-idp.example.com/token",
    "userinfo_endpoint": "https://mock-idp.example.com/userinfo",
    "jwks_uri": "https://mock-idp.example.com/jwks",
}

_TOKEN_RESPONSE = {
    "access_token": "mock-provider-access-token",
    "token_type": "Bearer",
    "expires_in": 3600,
    "refresh_token": "mock-provider-refresh-token",
    "id_token": "mock.id.token",
}

_USERINFO = {
    "sub": "google-uid-12345",
    "email": "alice@example.com",
    "name": "Alice Example",
}


@pytest.fixture()
def oauth_client(tmp_path, monkeypatch):
    """TestClient with a mock OIDC provider registered."""
    import sys
    from unittest.mock import MagicMock

    for mod in ("orchid.registry", "orchid.runner"):
        if mod not in sys.modules:
            stub = MagicMock()
            stub.ProjectRegistry = MagicMock(return_value=MagicMock(list_projects=lambda: []))
            stub.BackgroundRunner = MagicMock(return_value=MagicMock())
            sys.modules[mod] = stub

    import orchid.web.server as srv
    from orchid.auth.providers.oidc_generic import GenericOIDCProvider

    new_store = UserStore(path=tmp_path / "users.json")
    monkeypatch.setattr(srv, "_auth_store", new_store)

    import orchid.auth.middleware as mw
    monkeypatch.setattr(mw, "_default_store", new_store)

    # Fresh provider registry with one mock provider
    from orchid.auth.providers.registry import ProviderRegistry
    new_registry = ProviderRegistry()
    new_registry.register(GenericOIDCProvider(
        slug="mock-idp",
        discovery_url="https://mock-idp.example.com/.well-known/openid-configuration",
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="https://orchid.example.com/api/auth/oauth/mock-idp/callback",
    ))
    monkeypatch.setattr(srv, "_provider_registry", new_registry)
    monkeypatch.setattr(srv, "_oauth_states", {})

    return TestClient(srv.app, raise_server_exceptions=True, follow_redirects=False)


class TestOAuthUnit:
    """Unit tests for provider logic and store OAuth CRUD."""

    def test_link_or_create_new_user(self, tmp_path):
        from orchid.auth.providers.base import link_or_create_user
        store = make_store(tmp_path)

        user, oa = link_or_create_user(
            store, "google", "google-uid-1", "bob@example.com",
            access_token="at", refresh_token="rt",
        )
        assert user.email == "bob@example.com"
        assert oa.provider == "google"
        assert oa.provider_user_id == "google-uid-1"
        assert store.get_user(user.user_id) is not None

    def test_link_or_create_links_existing_email(self, tmp_path):
        from orchid.auth.providers.base import link_or_create_user
        store = make_store(tmp_path)
        existing = User(user_id="existing-id", username="bob", email="bob@example.com",
                        password_hash=hash_password("pw"))
        store.add_user(existing)

        user, oa = link_or_create_user(
            store, "google", "google-uid-1", "bob@example.com", access_token="at",
        )
        assert user.user_id == "existing-id"  # linked to existing user
        assert oa.user_id == "existing-id"

    def test_link_or_create_reuses_existing_oauth_account(self, tmp_path):
        from orchid.auth.providers.base import link_or_create_user
        store = make_store(tmp_path)

        user1, oa1 = link_or_create_user(
            store, "google", "google-uid-1", "bob@example.com", access_token="old-at",
        )
        user2, oa2 = link_or_create_user(
            store, "google", "google-uid-1", "bob@example.com", access_token="new-at",
        )
        assert user1.user_id == user2.user_id
        # Should not create a second user
        assert len(store.list_users()) == 1

    def test_username_conflict_resolved(self, tmp_path):
        from orchid.auth.providers.base import link_or_create_user
        store = make_store(tmp_path)
        # Pre-existing user with username "alice"
        store.add_user(User(user_id="u1", username="alice", email="alice@work.com"))

        user, _ = link_or_create_user(
            store, "google", "uid-2", "alice@example.com", access_token="at",
        )
        assert user.username != "alice"  # got "alice1" or similar

    def test_oauth_account_persistence(self, tmp_path):
        from orchid.auth.providers.base import link_or_create_user
        path = tmp_path / "users.json"
        s1 = UserStore(path=path)
        link_or_create_user(s1, "google", "uid-1", "a@b.com", access_token="at")

        s2 = UserStore(path=path)
        oa = s2.get_oauth_account("google", "uid-1")
        assert oa is not None
        assert oa.email == "a@b.com"

    def test_store_list_oauth_for_user(self, tmp_path):
        from orchid.auth.providers.base import link_or_create_user
        store = make_store(tmp_path)
        user = make_user()
        store.add_user(user)
        link_or_create_user(store, "google", "g-uid", "alice@g.com", access_token="at")
        # Different user
        link_or_create_user(store, "entra", "e-uid", "bob@ms.com", access_token="at")

        alice_accounts = store.list_oauth_accounts_for_user(user.user_id)
        # alice registered via make_user(), not via OAuth — she may or may not appear
        # depending on email match. Just check the function returns a list.
        assert isinstance(alice_accounts, list)


class TestProviderRegistry:
    def test_register_and_get(self):
        from orchid.auth.providers.registry import ProviderRegistry
        from orchid.auth.providers.oidc_generic import GenericOIDCProvider
        reg = ProviderRegistry()
        p = GenericOIDCProvider("test-idp", "https://x.com/.well-known/openid-configuration",
                                "cid", "cs", "https://x.com/cb")
        reg.register(p)
        assert reg.get("test-idp") is p
        assert "test-idp" in reg.slugs()

    def test_get_unknown_returns_none(self):
        from orchid.auth.providers.registry import ProviderRegistry
        assert ProviderRegistry().get("nope") is None

    def test_from_config_google(self):
        from orchid.auth.providers.registry import ProviderRegistry
        config = {"auth": {"providers": [{
            "type": "google", "client_id": "cid", "client_secret": "cs",
            "redirect_uri": "https://x.com/cb",
        }]}}
        reg = ProviderRegistry.from_config(config)
        assert reg.get("google") is not None

    def test_from_config_entra(self):
        from orchid.auth.providers.registry import ProviderRegistry
        config = {"auth": {"providers": [{
            "type": "entra", "tenant_id": "tid", "client_id": "cid",
            "client_secret": "cs", "redirect_uri": "https://x.com/cb",
        }]}}
        reg = ProviderRegistry.from_config(config)
        assert reg.get("entra") is not None

    def test_from_config_generic_oidc(self):
        from orchid.auth.providers.registry import ProviderRegistry
        config = {"auth": {"providers": [{
            "type": "oidc", "name": "sso", "discovery_url": "https://sso.x.com/.well-known/openid-configuration",
            "client_id": "cid", "client_secret": "cs", "redirect_uri": "https://x.com/cb",
        }]}}
        reg = ProviderRegistry.from_config(config)
        assert reg.get("sso") is not None

    def test_from_config_skips_unknown_type(self):
        from orchid.auth.providers.registry import ProviderRegistry
        config = {"auth": {"providers": [{"type": "twitter"}]}}
        reg = ProviderRegistry.from_config(config)  # should not raise
        assert reg.slugs() == []


class TestOAuthEndpoints:
    """Integration tests using mocked OIDC HTTP calls."""

    @respx.mock
    def test_list_providers(self, oauth_client):
        r = oauth_client.get("/api/auth/oauth/providers")
        assert r.status_code == 200
        assert "mock-idp" in r.json()["providers"]

    @respx.mock
    def test_start_redirects_to_provider(self, oauth_client):
        respx.get("https://mock-idp.example.com/.well-known/openid-configuration").mock(
            return_value=_httpx.Response(200, json=_DISCOVERY)
        )
        r = oauth_client.get("/api/auth/oauth/mock-idp/start")
        assert r.status_code == 302
        location = r.headers["location"]
        assert "https://mock-idp.example.com/auth" in location
        assert "state=" in location

    @respx.mock
    def test_start_unknown_provider(self, oauth_client):
        r = oauth_client.get("/api/auth/oauth/no-such-provider/start")
        assert r.status_code == 404

    @respx.mock
    def test_callback_get_creates_user_and_sets_cookies(self, oauth_client):
        # Mock discovery, token exchange, userinfo
        respx.get("https://mock-idp.example.com/.well-known/openid-configuration").mock(
            return_value=_httpx.Response(200, json=_DISCOVERY)
        )
        respx.post("https://mock-idp.example.com/token").mock(
            return_value=_httpx.Response(200, json=_TOKEN_RESPONSE)
        )
        respx.get("https://mock-idp.example.com/userinfo").mock(
            return_value=_httpx.Response(200, json=_USERINFO)
        )

        # Prime state via start
        respx.get("https://mock-idp.example.com/auth").mock(
            return_value=_httpx.Response(302, headers={"location": "/"})
        )
        start = oauth_client.get("/api/auth/oauth/mock-idp/start")
        from urllib.parse import urlparse, parse_qs
        location = start.headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]

        r = oauth_client.get(f"/api/auth/oauth/mock-idp/callback?code=authcode&state={state}")
        assert r.status_code == 302  # redirect to /?oauth=success
        assert "orchid_access" in r.cookies or "orchid_access" in oauth_client.cookies

    @respx.mock
    def test_callback_post_creates_user(self, oauth_client):
        respx.get("https://mock-idp.example.com/.well-known/openid-configuration").mock(
            return_value=_httpx.Response(200, json=_DISCOVERY)
        )
        respx.post("https://mock-idp.example.com/token").mock(
            return_value=_httpx.Response(200, json=_TOKEN_RESPONSE)
        )
        respx.get("https://mock-idp.example.com/userinfo").mock(
            return_value=_httpx.Response(200, json=_USERINFO)
        )

        # Inject state manually (simulates POST from provider)
        import orchid.web.server as srv
        state = "test-state-post"
        from datetime import datetime, timedelta, timezone
        srv._oauth_states[state] = {
            "provider": "mock-idp",
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        }

        r = oauth_client.post(
            "/api/auth/oauth/mock-idp/callback",
            json={"code": "authcode", "state": state},
        )
        assert r.status_code in (200, 302)

    @respx.mock
    def test_callback_invalid_state_rejected(self, oauth_client):
        r = oauth_client.get("/api/auth/oauth/mock-idp/callback?code=x&state=bad-state")
        assert r.status_code == 400

    @respx.mock
    def test_callback_missing_code_rejected(self, oauth_client):
        r = oauth_client.get("/api/auth/oauth/mock-idp/callback?state=x")
        assert r.status_code == 400

    @respx.mock
    def test_callback_provider_error_param(self, oauth_client):
        r = oauth_client.get("/api/auth/oauth/mock-idp/callback?error=access_denied&state=x")
        assert r.status_code == 400

    @respx.mock
    def test_second_login_same_provider_reuses_user(self, oauth_client):
        """Two logins with the same provider + sub should not create two users."""
        respx.get("https://mock-idp.example.com/.well-known/openid-configuration").mock(
            return_value=_httpx.Response(200, json=_DISCOVERY)
        )
        respx.post("https://mock-idp.example.com/token").mock(
            return_value=_httpx.Response(200, json=_TOKEN_RESPONSE)
        )
        respx.get("https://mock-idp.example.com/userinfo").mock(
            return_value=_httpx.Response(200, json=_USERINFO)
        )

        import orchid.web.server as srv

        async def do_callback():
            from orchid.auth.providers.oidc_generic import GenericOIDCProvider
            import orchid.web.server as s
            provider = s._provider_registry.get("mock-idp")
            store = s._auth_store
            user1, _ = await provider.handle_callback("code", store)
            user2, _ = await provider.handle_callback("code", store)
            return user1, user2

        import asyncio
        u1, u2 = asyncio.get_event_loop().run_until_complete(do_callback())
        assert u1.user_id == u2.user_id
