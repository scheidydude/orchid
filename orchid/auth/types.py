from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class AuthError(Exception):
    """Raised on auth-related failures (duplicate user, invalid token, etc.)."""


@dataclass
class User:
    """Represents an authenticated user in Orchid."""
    user_id: str
    username: str = ""
    email: Optional[str] = None
    role: str = "user"  # "user", "admin", "readonly"
    created_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True
    projects: list = field(default_factory=list)
    api_keys: dict = field(default_factory=dict)
    budget_usd: float = 0.0
    cpu_budget_seconds: float = 0.0  # Phase 6: daily CPU-seconds cap (0 = unlimited)
    password_hash: Optional[str] = None
    token: str = ""  # legacy field — superseded by JWT; kept for backward compat


@dataclass
class AuthToken:
    """Legacy bearer token — superseded by JWT in Phase 1. Kept for backward compat."""
    token: str
    user_id: str
    expires_at: datetime
    created_at: datetime = field(default_factory=datetime.now)
    is_valid: bool = True


@dataclass
class RefreshToken:
    """Persisted refresh token record. The raw token is never stored — only its hash."""
    token_id: str        # UUID; embedded in the raw token for O(1) lookup
    user_id: str
    token_hash: str      # argon2 hash of the secret portion of the raw token
    expires_at: datetime
    created_at: datetime = field(default_factory=datetime.now)
    is_revoked: bool = False


@dataclass
class OAuthAccount:
    """Links an external OIDC identity to an Orchid user.

    Key: '{provider}:{provider_user_id}' — uniquely identifies one social login.
    """
    provider: str            # slug: "google", "entra", "company-sso"
    provider_user_id: str    # 'sub' from OIDC userinfo
    user_id: str             # Orchid user_id
    email: str
    access_token: str        # provider's access token (may expire)
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ApiKey:
    """Persisted API key record for programmatic/CI access.

    Raw key format: 'ok_{key_id}.{secret}' — prefix enables quick detection,
    key_id enables O(1) store lookup, secret is argon2-hashed before storage.
    """
    key_id: str
    secret_hash: str     # argon2 hash of the secret portion
    user_id: str
    name: str            # human-readable label
    scopes: list = field(default_factory=list)   # e.g. ["tasks:run", "tasks:read"]
    created_at: datetime = field(default_factory=datetime.now)
    last_used: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_active: bool = True


@dataclass
class AuditEvent:
    """Immutable record of a security-relevant action.

    Stored append-only in daily JSONL files — never modified or deleted.
    """
    event_id: str
    user_id: str     # "anonymous" for unauthenticated requests
    action: str      # see AuditAction in orchid/auth/audit.py
    resource: str    # project path, user_id, key_id, etc.
    result: str      # "success" | "failure" | "denied"
    timestamp: datetime
    ip: str          # client IP address
    detail: str = ""  # optional JSON string with extra context
