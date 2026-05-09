from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class AuthError(Exception):
    """Raised on auth-related failures (duplicate user, invalid token, etc.)."""


@dataclass
class User:
    """Represents an authenticated user in Orchid."""
    user_id: str
    token: str
    username: str = ""
    email: Optional[str] = None
    role: str = "user"  # "user", "admin", "readonly"
    created_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True
    projects: list = field(default_factory=list)
    api_keys: dict = field(default_factory=dict)
    budget_usd: float = 0.0


@dataclass
class AuthToken:
    """Represents an authentication token."""
    token: str
    user_id: str
    expires_at: datetime
    created_at: datetime = field(default_factory=datetime.now)
    is_valid: bool = True