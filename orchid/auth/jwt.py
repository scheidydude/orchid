"""JWT and password utilities for Orchid auth (Phase 1).

Access tokens: HS256 JWT, 15-minute TTL, self-contained (no DB hit to verify).
Refresh tokens: opaque '{token_id}.{secret}' — token_id enables O(1) store lookup,
  secret is argon2-hashed before storage.
Passwords: argon2id with OWASP-recommended parameters.
"""
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import jwt as _pyjwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from orchid.auth.types import AuthError, RefreshToken, User

if TYPE_CHECKING:
    from orchid.auth.store import UserStore

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)

_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def _jwt_secret() -> str:
    s = os.environ.get("JWT_SECRET", "")
    if not s:
        raise RuntimeError("JWT_SECRET environment variable not set")
    return s


# ── passwords ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _ph.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False


# ── access tokens ─────────────────────────────────────────────────────────────

def issue_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.user_id,
        "role": user.role,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
    }
    return _pyjwt.encode(payload, _jwt_secret(), algorithm="HS256")


def verify_access_token(token: str) -> dict:
    """Decode and verify a JWT access token. Raises AuthError on failure."""
    try:
        return _pyjwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except _pyjwt.ExpiredSignatureError:
        raise AuthError("Token expired")
    except _pyjwt.InvalidTokenError as exc:
        raise AuthError(f"Invalid token: {exc}")


# ── refresh tokens ────────────────────────────────────────────────────────────

def issue_refresh_token(user: User) -> tuple[str, RefreshToken]:
    """Create a refresh token pair.

    Returns:
        raw: opaque string to send to the client — '{token_id}.{secret}'
        rt:  RefreshToken record to persist in the store (contains hash, not secret)
    """
    token_id = str(uuid.uuid4())
    secret = secrets.token_urlsafe(48)
    raw = f"{token_id}.{secret}"
    now = datetime.now(timezone.utc)
    rt = RefreshToken(
        token_id=token_id,
        user_id=user.user_id,
        token_hash=_ph.hash(secret),
        expires_at=now + REFRESH_TOKEN_TTL,
        created_at=now,
    )
    return raw, rt


def verify_refresh_token(raw: str, store: "UserStore") -> RefreshToken:
    """Validate a raw refresh token.  Returns the store record on success."""
    try:
        token_id, secret = raw.split(".", 1)
    except ValueError:
        raise AuthError("Malformed refresh token")

    rt = store.get_refresh_token(token_id)
    if rt is None:
        raise AuthError("Refresh token not found")
    if rt.is_revoked:
        raise AuthError("Refresh token revoked")

    expires = rt.expires_at
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise AuthError("Refresh token expired")

    try:
        _ph.verify(rt.token_hash, secret)
    except VerifyMismatchError:
        raise AuthError("Invalid refresh token")

    return rt
