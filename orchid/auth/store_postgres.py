"""PostgreSQL-backed UserStore for multi-node / enterprise deployments.

Requires psycopg2-binary:
    uv pip install 'orchid[postgres]'

Set ORCHID_AUTH_STORE_DSN to a libpq connection string and get_store()
will automatically use this backend:
    ORCHID_AUTH_STORE_DSN=postgresql://user:pass@host:5432/orchid

Tables are created on first connect (CREATE TABLE IF NOT EXISTS).
All table names are prefixed with `orchid_` to avoid conflicts.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from orchid.auth.base import BaseUserStore
from orchid.auth.types import ApiKey, AuthError, OAuthAccount, RefreshToken, User

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchid_users (
    user_id      TEXT PRIMARY KEY,
    username     TEXT UNIQUE NOT NULL,
    email        TEXT,
    role         TEXT NOT NULL DEFAULT 'user',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    projects     JSONB NOT NULL DEFAULT '[]',
    api_keys     JSONB NOT NULL DEFAULT '{}',
    budget_usd   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    password_hash TEXT,
    token        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS orchid_refresh_tokens (
    token_id   TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS orchid_api_keys (
    key_id      TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    scopes      JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used   TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS orchid_oauth_accounts (
    provider         TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    email            TEXT NOT NULL,
    access_token     TEXT NOT NULL,
    refresh_token    TEXT,
    expires_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, provider_user_id)
);
"""


def _row_to_user(row: dict) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        email=row["email"],
        role=row["role"],
        created_at=row["created_at"],
        is_active=row["is_active"],
        projects=row["projects"] or [],
        api_keys=row["api_keys"] or {},
        budget_usd=row["budget_usd"],
        password_hash=row["password_hash"],
        token=row["token"] or "",
    )


def _row_to_rt(row: dict) -> RefreshToken:
    return RefreshToken(
        token_id=row["token_id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        is_revoked=row["is_revoked"],
    )


def _row_to_ak(row: dict) -> ApiKey:
    return ApiKey(
        key_id=row["key_id"],
        secret_hash=row["secret_hash"],
        user_id=row["user_id"],
        name=row["name"],
        scopes=row["scopes"] or [],
        created_at=row["created_at"],
        last_used=row["last_used"],
        expires_at=row["expires_at"],
        is_active=row["is_active"],
    )


def _row_to_oa(row: dict) -> OAuthAccount:
    return OAuthAccount(
        provider=row["provider"],
        provider_user_id=row["provider_user_id"],
        user_id=row["user_id"],
        email=row["email"],
        access_token=row["access_token"],
        refresh_token=row["refresh_token"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


class PostgresUserStore(BaseUserStore):
    """UserStore backed by PostgreSQL. Thread-safe via connection pool."""

    def __init__(self, dsn: str, minconn: int = 2, maxconn: int = 10) -> None:
        self._pool = ThreadedConnectionPool(minconn, maxconn, dsn,
                                            cursor_factory=psycopg2.extras.RealDictCursor)
        self._init_schema()
        logger.info("PostgresUserStore connected (pool %d–%d)", minconn, maxconn)

    def _init_schema(self) -> None:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            conn.commit()
        finally:
            self._pool.putconn(conn)

    def _conn(self):
        return _PoolConn(self._pool)

    # ── users ─────────────────────────────────────────────────────────────────

    def add_user(self, user: User) -> None:
        with self._conn() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO orchid_users
                        (user_id, username, email, role, created_at, is_active,
                         projects, api_keys, budget_usd, password_hash, token)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (user.user_id, user.username, user.email, user.role,
                     user.created_at, user.is_active,
                     json.dumps(user.projects), json.dumps(user.api_keys),
                     user.budget_usd, user.password_hash, user.token),
                )
            except psycopg2.errors.UniqueViolation:
                raise AuthError(f"User {user.user_id!r} already exists")

    def update_user(self, user: User) -> None:
        with self._conn() as cur:
            cur.execute(
                """
                UPDATE orchid_users SET
                    username=%s, email=%s, role=%s, is_active=%s,
                    projects=%s, api_keys=%s, budget_usd=%s,
                    password_hash=%s, token=%s
                WHERE user_id=%s
                """,
                (user.username, user.email, user.role, user.is_active,
                 json.dumps(user.projects), json.dumps(user.api_keys),
                 user.budget_usd, user.password_hash, user.token,
                 user.user_id),
            )
            if cur.rowcount == 0:
                raise AuthError(f"User {user.user_id!r} not found")

    def remove_user(self, user_id: str) -> None:
        with self._conn() as cur:
            cur.execute("DELETE FROM orchid_users WHERE user_id=%s", (user_id,))

    def delete_user(self, user_id: str) -> bool:
        with self._conn() as cur:
            cur.execute("DELETE FROM orchid_users WHERE user_id=%s", (user_id,))
            return cur.rowcount > 0

    def list_users(self) -> list[User]:
        with self._conn() as cur:
            cur.execute("SELECT * FROM orchid_users ORDER BY created_at")
            return [_row_to_user(r) for r in cur.fetchall()]

    def get_user(self, user_id: str) -> User | None:
        with self._conn() as cur:
            cur.execute("SELECT * FROM orchid_users WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            return _row_to_user(row) if row else None

    def get_by_id(self, user_id: str) -> User:
        user = self.get_user(user_id)
        if user is None:
            raise AuthError(f"User {user_id} not found")
        return user

    def get_by_token(self, token: str) -> User:
        with self._conn() as cur:
            cur.execute("SELECT * FROM orchid_users WHERE token=%s", (token,))
            row = cur.fetchone()
        if row is None:
            raise AuthError("Invalid token")
        return _row_to_user(row)

    def get_user_by_username(self, username: str) -> User | None:
        with self._conn() as cur:
            cur.execute("SELECT * FROM orchid_users WHERE username=%s", (username,))
            row = cur.fetchone()
            return _row_to_user(row) if row else None

    def get_user_by_email(self, email: str) -> User | None:
        with self._conn() as cur:
            cur.execute("SELECT * FROM orchid_users WHERE lower(email)=lower(%s)", (email,))
            row = cur.fetchone()
            return _row_to_user(row) if row else None

    # ── refresh tokens ────────────────────────────────────────────────────────

    def store_refresh_token(self, rt: RefreshToken) -> None:
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO orchid_refresh_tokens
                    (token_id, user_id, token_hash, expires_at, created_at, is_revoked)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (token_id) DO UPDATE SET
                    token_hash=EXCLUDED.token_hash,
                    expires_at=EXCLUDED.expires_at,
                    is_revoked=EXCLUDED.is_revoked
                """,
                (rt.token_id, rt.user_id, rt.token_hash,
                 rt.expires_at, rt.created_at, rt.is_revoked),
            )

    def get_refresh_token(self, token_id: str) -> RefreshToken | None:
        with self._conn() as cur:
            cur.execute("SELECT * FROM orchid_refresh_tokens WHERE token_id=%s", (token_id,))
            row = cur.fetchone()
            return _row_to_rt(row) if row else None

    def revoke_refresh_token(self, token_id: str) -> None:
        with self._conn() as cur:
            cur.execute(
                "UPDATE orchid_refresh_tokens SET is_revoked=TRUE WHERE token_id=%s",
                (token_id,),
            )

    def revoke_all_refresh_tokens(self, user_id: str) -> None:
        with self._conn() as cur:
            cur.execute(
                "UPDATE orchid_refresh_tokens SET is_revoked=TRUE WHERE user_id=%s",
                (user_id,),
            )

    # ── API keys ──────────────────────────────────────────────────────────────

    def store_api_key(self, key: ApiKey) -> None:
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO orchid_api_keys
                    (key_id, secret_hash, user_id, name, scopes,
                     created_at, last_used, expires_at, is_active)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (key_id) DO UPDATE SET
                    secret_hash=EXCLUDED.secret_hash,
                    scopes=EXCLUDED.scopes,
                    is_active=EXCLUDED.is_active
                """,
                (key.key_id, key.secret_hash, key.user_id, key.name,
                 json.dumps(key.scopes), key.created_at,
                 key.last_used, key.expires_at, key.is_active),
            )

    def get_api_key(self, key_id: str) -> ApiKey | None:
        with self._conn() as cur:
            cur.execute("SELECT * FROM orchid_api_keys WHERE key_id=%s", (key_id,))
            row = cur.fetchone()
            return _row_to_ak(row) if row else None

    def list_api_keys(self, user_id: str) -> list[ApiKey]:
        with self._conn() as cur:
            cur.execute("SELECT * FROM orchid_api_keys WHERE user_id=%s ORDER BY created_at",
                        (user_id,))
            return [_row_to_ak(r) for r in cur.fetchall()]

    def revoke_api_key(self, key_id: str) -> bool:
        with self._conn() as cur:
            cur.execute(
                "UPDATE orchid_api_keys SET is_active=FALSE WHERE key_id=%s", (key_id,)
            )
            return cur.rowcount > 0

    def touch_api_key(self, key_id: str) -> None:
        with self._conn() as cur:
            cur.execute(
                "UPDATE orchid_api_keys SET last_used=NOW() WHERE key_id=%s", (key_id,)
            )

    # ── OAuth accounts ────────────────────────────────────────────────────────

    def store_oauth_account(self, oa: OAuthAccount) -> None:
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO orchid_oauth_accounts
                    (provider, provider_user_id, user_id, email,
                     access_token, refresh_token, expires_at, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (provider, provider_user_id) DO UPDATE SET
                    user_id=EXCLUDED.user_id,
                    email=EXCLUDED.email,
                    access_token=EXCLUDED.access_token,
                    refresh_token=EXCLUDED.refresh_token,
                    expires_at=EXCLUDED.expires_at
                """,
                (oa.provider, oa.provider_user_id, oa.user_id, oa.email,
                 oa.access_token, oa.refresh_token, oa.expires_at, oa.created_at),
            )

    def get_oauth_account(self, provider: str, provider_user_id: str) -> OAuthAccount | None:
        with self._conn() as cur:
            cur.execute(
                "SELECT * FROM orchid_oauth_accounts WHERE provider=%s AND provider_user_id=%s",
                (provider, provider_user_id),
            )
            row = cur.fetchone()
            return _row_to_oa(row) if row else None

    def list_oauth_accounts_for_user(self, user_id: str) -> list[OAuthAccount]:
        with self._conn() as cur:
            cur.execute(
                "SELECT * FROM orchid_oauth_accounts WHERE user_id=%s", (user_id,)
            )
            return [_row_to_oa(r) for r in cur.fetchall()]


class _PoolConn:
    """Context manager: borrow a connection, auto-commit or rollback, return to pool."""

    def __init__(self, pool: ThreadedConnectionPool) -> None:
        self._pool = pool
        self._conn = None

    def __enter__(self):
        self._conn = self._pool.getconn()
        self._cur = self._conn.cursor()
        return self._cur

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._cur.close()
        self._pool.putconn(self._conn)
        return False
