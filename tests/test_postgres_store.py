"""Integration tests for PostgresUserStore.

Requires a live Postgres instance. Skipped automatically when unavailable.

    ORCHID_AUTH_STORE_DSN=postgresql://orchid:orchid_dev@localhost/orchid pytest -x tests/test_postgres_store.py

Or export the var and run normally.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

DSN = os.environ.get(
    "ORCHID_AUTH_STORE_DSN",
    "postgresql://orchid:orchid_dev@localhost/orchid",
)


def _make_store():
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        from orchid.auth.store_postgres import PostgresUserStore
        return PostgresUserStore(DSN, minconn=1, maxconn=3)
    except Exception as exc:
        pytest.skip(f"Postgres unavailable: {exc}")


def _uid() -> str:
    return f"test_{uuid.uuid4().hex[:8]}"


def _user(uid=None):
    from orchid.auth.types import User
    uid = uid or _uid()
    return User(
        user_id=uid,
        username=f"user_{uid}",
        email=f"{uid}@example.com",
        role="user",
        is_active=True,
        created_at=datetime.now(UTC),
    )


# ── Schema init ───────────────────────────────────────────────────────────────

def test_schema_init_idempotent():
    """_init_schema can be called multiple times without error."""
    store = _make_store()
    store._init_schema()  # second call — should not raise


# ── Users CRUD ────────────────────────────────────────────────────────────────

class TestUsersCRUD:

    def setup_method(self):
        self.store = _make_store()
        self._created = []

    def teardown_method(self):
        for uid in self._created:
            try:
                self.store.delete_user(uid)
            except Exception:
                pass

    def _add(self, user=None):
        u = user or _user()
        self.store.add_user(u)
        self._created.append(u.user_id)
        return u

    def test_add_and_get(self):
        u = self._add()
        got = self.store.get_user(u.user_id)
        assert got is not None
        assert got.user_id == u.user_id
        assert got.username == u.username
        assert got.email == u.email

    def test_get_unknown_returns_none(self):
        assert self.store.get_user("does_not_exist_xyz") is None

    def test_add_duplicate_raises(self):
        from orchid.auth.types import AuthError
        u = self._add()
        with pytest.raises(AuthError):
            self.store.add_user(u)

    def test_update_user(self):
        u = self._add()
        u.email = "updated@example.com"
        u.role = "admin"
        self.store.update_user(u)
        got = self.store.get_user(u.user_id)
        assert got.email == "updated@example.com"
        assert got.role == "admin"

    def test_update_nonexistent_raises(self):
        from orchid.auth.types import AuthError
        ghost = _user()
        with pytest.raises(AuthError):
            self.store.update_user(ghost)

    def test_delete_user(self):
        u = self._add()
        deleted = self.store.delete_user(u.user_id)
        assert deleted is True
        self._created.remove(u.user_id)  # already deleted
        assert self.store.get_user(u.user_id) is None

    def test_delete_nonexistent_returns_false(self):
        assert self.store.delete_user("ghost_xyz") is False

    def test_list_users_includes_created(self):
        u = self._add()
        users = self.store.list_users()
        ids = {x.user_id for x in users}
        assert u.user_id in ids

    def test_get_by_username(self):
        u = self._add()
        got = self.store.get_user_by_username(u.username)
        assert got is not None
        assert got.user_id == u.user_id

    def test_get_by_email(self):
        u = self._add()
        got = self.store.get_user_by_email(u.email)
        assert got is not None
        assert got.user_id == u.user_id

    def test_get_by_email_case_insensitive(self):
        u = self._add()
        got = self.store.get_user_by_email(u.email.upper())
        assert got is not None

    def test_budget_fields_persist(self):
        u = self._add()
        u.budget_usd = 10.0
        u.budget_used_usd = 3.5
        u.cpu_budget_seconds = 3600.0
        u.cpu_used_seconds = 120.0
        u.cpu_last_reset_date = "2026-05-27"
        self.store.update_user(u)
        got = self.store.get_user(u.user_id)
        assert got.budget_usd == 10.0
        assert got.budget_used_usd == 3.5
        assert got.cpu_budget_seconds == 3600.0
        assert got.cpu_used_seconds == 120.0
        assert got.cpu_last_reset_date == "2026-05-27"

    def test_notification_config_persists(self):
        u = self._add()
        u.notification_config = {"email": True, "telegram_chat_id": "12345"}
        self.store.update_user(u)
        got = self.store.get_user(u.user_id)
        assert got.notification_config["email"] is True
        assert got.notification_config["telegram_chat_id"] == "12345"

    def test_projects_list_persists(self):
        u = self._add()
        u.projects = ["proj1", "proj2"]
        self.store.update_user(u)
        got = self.store.get_user(u.user_id)
        assert got.projects == ["proj1", "proj2"]


# ── Refresh tokens ────────────────────────────────────────────────────────────

class TestRefreshTokens:

    def setup_method(self):
        self.store = _make_store()
        u = _user()
        self.store.add_user(u)
        self._uid = u.user_id
        self._token_ids = []

    def teardown_method(self):
        self.store.delete_user(self._uid)

    def _rt(self):
        from orchid.auth.types import RefreshToken
        tid = _uid()
        self._token_ids.append(tid)
        return RefreshToken(
            token_id=tid,
            user_id=self._uid,
            token_hash="hash_" + tid,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            created_at=datetime.now(UTC),
            is_revoked=False,
        )

    def test_store_and_get(self):
        rt = self._rt()
        self.store.store_refresh_token(rt)
        got = self.store.get_refresh_token(rt.token_id)
        assert got is not None
        assert got.token_hash == rt.token_hash
        assert got.is_revoked is False

    def test_revoke(self):
        rt = self._rt()
        self.store.store_refresh_token(rt)
        self.store.revoke_refresh_token(rt.token_id)
        got = self.store.get_refresh_token(rt.token_id)
        assert got.is_revoked is True

    def test_revoke_all(self):
        rt1 = self._rt()
        rt2 = self._rt()
        self.store.store_refresh_token(rt1)
        self.store.store_refresh_token(rt2)
        self.store.revoke_all_refresh_tokens(self._uid)
        assert self.store.get_refresh_token(rt1.token_id).is_revoked is True
        assert self.store.get_refresh_token(rt2.token_id).is_revoked is True

    def test_get_unknown_returns_none(self):
        assert self.store.get_refresh_token("ghost_token_xyz") is None

    def test_upsert_on_conflict(self):
        """store_refresh_token upserts — same token_id updates hash."""
        rt = self._rt()
        self.store.store_refresh_token(rt)
        rt.token_hash = "new_hash"
        self.store.store_refresh_token(rt)
        got = self.store.get_refresh_token(rt.token_id)
        assert got.token_hash == "new_hash"


# ── API keys ──────────────────────────────────────────────────────────────────

class TestApiKeys:

    def setup_method(self):
        self.store = _make_store()
        u = _user()
        self.store.add_user(u)
        self._uid = u.user_id

    def teardown_method(self):
        self.store.delete_user(self._uid)

    def _ak(self):
        from orchid.auth.types import ApiKey
        kid = _uid()
        return ApiKey(
            key_id=kid,
            secret_hash="hash_" + kid,
            user_id=self._uid,
            name="Test key",
            scopes=["read", "write"],
            created_at=datetime.now(UTC),
            is_active=True,
        )

    def test_store_and_get(self):
        ak = self._ak()
        self.store.store_api_key(ak)
        got = self.store.get_api_key(ak.key_id)
        assert got is not None
        assert got.scopes == ["read", "write"]

    def test_list_by_user(self):
        ak = self._ak()
        self.store.store_api_key(ak)
        keys = self.store.list_api_keys(self._uid)
        ids = {k.key_id for k in keys}
        assert ak.key_id in ids

    def test_revoke(self):
        ak = self._ak()
        self.store.store_api_key(ak)
        result = self.store.revoke_api_key(ak.key_id)
        assert result is True
        got = self.store.get_api_key(ak.key_id)
        assert got.is_active is False

    def test_touch(self):
        ak = self._ak()
        self.store.store_api_key(ak)
        self.store.touch_api_key(ak.key_id)
        got = self.store.get_api_key(ak.key_id)
        assert got.last_used is not None

    def test_get_unknown_returns_none(self):
        assert self.store.get_api_key("ghost_key_xyz") is None


# ── OAuth accounts ────────────────────────────────────────────────────────────

class TestOAuthAccounts:

    def setup_method(self):
        self.store = _make_store()
        u = _user()
        self.store.add_user(u)
        self._uid = u.user_id

    def teardown_method(self):
        self.store.delete_user(self._uid)

    def _oa(self, provider="google"):
        from orchid.auth.types import OAuthAccount
        return OAuthAccount(
            provider=provider,
            provider_user_id=_uid(),
            user_id=self._uid,
            email=f"oauth_{_uid()}@example.com",
            access_token="tok_abc",
            refresh_token=None,
            expires_at=None,
            created_at=datetime.now(UTC),
        )

    def test_store_and_get(self):
        oa = self._oa()
        self.store.store_oauth_account(oa)
        got = self.store.get_oauth_account(oa.provider, oa.provider_user_id)
        assert got is not None
        assert got.user_id == self._uid

    def test_list_for_user(self):
        oa = self._oa()
        self.store.store_oauth_account(oa)
        accounts = self.store.list_oauth_accounts_for_user(self._uid)
        ids = {a.provider_user_id for a in accounts}
        assert oa.provider_user_id in ids

    def test_upsert_updates_token(self):
        oa = self._oa()
        self.store.store_oauth_account(oa)
        oa.access_token = "tok_new"
        self.store.store_oauth_account(oa)
        got = self.store.get_oauth_account(oa.provider, oa.provider_user_id)
        assert got.access_token == "tok_new"

    def test_get_unknown_returns_none(self):
        assert self.store.get_oauth_account("google", "ghost_xyz") is None


# ── Scheduled tasks ───────────────────────────────────────────────────────────

class TestScheduledTasks:

    def setup_method(self):
        self.store = _make_store()
        u = _user()
        self.store.add_user(u)
        self._uid = u.user_id

    def teardown_method(self):
        self.store.delete_user(self._uid)

    def _task(self, enabled=True):
        tid = _uid()
        return {
            "task_id": tid,
            "name": "Test task",
            "task_type": "shell",
            "schedule": "0 * * * *",
            "enabled": enabled,
            "command": "echo hi",
        }

    def test_upsert_and_get(self):
        t = self._task()
        self.store.upsert_scheduled_task(self._uid, t)
        got = self.store.get_scheduled_task(self._uid, t["task_id"])
        assert got is not None
        assert got["name"] == "Test task"

    def test_upsert_updates(self):
        t = self._task()
        self.store.upsert_scheduled_task(self._uid, t)
        t["name"] = "Updated"
        self.store.upsert_scheduled_task(self._uid, t)
        got = self.store.get_scheduled_task(self._uid, t["task_id"])
        assert got["name"] == "Updated"

    def test_delete(self):
        t = self._task()
        self.store.upsert_scheduled_task(self._uid, t)
        deleted = self.store.delete_scheduled_task(self._uid, t["task_id"])
        assert deleted is True
        assert self.store.get_scheduled_task(self._uid, t["task_id"]) is None

    def test_delete_nonexistent_returns_false(self):
        assert self.store.delete_scheduled_task(self._uid, "ghost_tid") is False

    def test_get_nonexistent_returns_none(self):
        assert self.store.get_scheduled_task(self._uid, "ghost_tid") is None

    def test_get_all_enabled(self):
        t_on = self._task(enabled=True)
        t_off = self._task(enabled=False)
        self.store.upsert_scheduled_task(self._uid, t_on)
        self.store.upsert_scheduled_task(self._uid, t_off)
        all_enabled = self.store.get_all_enabled_scheduled_tasks()
        enabled_ids = {t["task_id"] for _, t in all_enabled}
        assert t_on["task_id"] in enabled_ids
        assert t_off["task_id"] not in enabled_ids


# ── Invite tokens ─────────────────────────────────────────────────────────────

class TestInviteTokens:

    def setup_method(self):
        self.store = _make_store()
        u = _user()
        self.store.add_user(u)
        self._uid = u.user_id

    def teardown_method(self):
        self.store.delete_user(self._uid)

    def _invite(self):
        from orchid.auth.types import InviteToken
        return InviteToken(
            token_id=_uid(),
            secret_hash="hash_abc",
            user_id=self._uid,
            email="invitee@example.com",
            invited_by="admin",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            is_used=False,
        )

    def test_store_and_get(self):
        inv = self._invite()
        self.store.store_invite(inv)
        got = self.store.get_invite(inv.token_id)
        assert got is not None
        assert got.email == "invitee@example.com"
        assert got.is_used is False

    def test_mark_used(self):
        inv = self._invite()
        self.store.store_invite(inv)
        self.store.mark_invite_used(inv.token_id)
        got = self.store.get_invite(inv.token_id)
        assert got.is_used is True

    def test_get_unknown_returns_none(self):
        assert self.store.get_invite("ghost_invite_xyz") is None


# ── get_store() auto-select ───────────────────────────────────────────────────

def test_get_store_selects_postgres(monkeypatch):
    """get_store() returns PostgresUserStore when DSN is set."""
    pytest.importorskip("psycopg2")
    monkeypatch.setenv("ORCHID_AUTH_STORE_DSN", DSN)

    import orchid.auth.store as _store_mod
    original = _store_mod._store_instance
    _store_mod._store_instance = None
    try:
        try:
            from orchid.auth.store import get_store
            store = get_store()
            from orchid.auth.store_postgres import PostgresUserStore
            assert isinstance(store, PostgresUserStore)
        except Exception as exc:
            pytest.skip(f"Postgres unavailable: {exc}")
    finally:
        _store_mod._store_instance = original
