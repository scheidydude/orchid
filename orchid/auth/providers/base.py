"""Base class and shared helpers for OIDC providers."""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from orchid.auth.types import AuthError, OAuthAccount, User

if TYPE_CHECKING:
    from orchid.auth.store import UserStore


class OIDCProvider(ABC):
    """Abstract base for all OIDC/OAuth2 providers."""

    @property
    @abstractmethod
    def slug(self) -> str:
        """URL-safe identifier used in /api/auth/oauth/{slug}/start."""

    @abstractmethod
    async def authorization_url(
        self,
        state: str,
        code_challenge: str = "",
        code_challenge_method: str = "S256",
    ) -> str:
        """Return the provider's authorization URL.

        When code_challenge is provided (PKCE/mobile flow), it is included
        in the authorization URL for the provider to verify later.
        """

    @abstractmethod
    async def handle_callback(
        self,
        code: str,
        store: "UserStore",
        code_verifier: str = "",
    ) -> tuple[User, OAuthAccount]:
        """Exchange authorization code → tokens → userinfo → (user, oauth_account).

        code_verifier is passed to the token endpoint when PKCE is in use.
        """


def link_or_create_user(
    store: "UserStore",
    provider: str,
    provider_user_id: str,
    email: str,
    access_token: str,
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[User, OAuthAccount]:
    """Find or create an Orchid user for the given OIDC identity.

    Priority:
    1. Existing OAuthAccount with same provider + provider_user_id → reuse.
    2. Existing User with same email → link the new OAuth account to them.
    3. Create a new User (no password — OAuth-only account).
    """
    existing_oa = store.get_oauth_account(provider, provider_user_id)
    if existing_oa:
        user = store.get_user(existing_oa.user_id)
        if user and user.is_active:
            # Refresh stored provider tokens
            existing_oa.access_token = access_token
            if refresh_token:
                existing_oa.refresh_token = refresh_token
            existing_oa.expires_at = expires_at
            store.store_oauth_account(existing_oa)
            return user, existing_oa

    user = store.get_user_by_email(email) if email else None

    if user is None:
        username = _derive_username(store, email, provider_user_id)
        user = User(
            user_id=str(uuid.uuid4()),
            username=username,
            email=email,
            role="user",
        )
        store.add_user(user)

    oa = OAuthAccount(
        provider=provider,
        provider_user_id=provider_user_id,
        user_id=user.user_id,
        email=email,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
    )
    store.store_oauth_account(oa)
    return user, oa


def _derive_username(store: "UserStore", email: str, fallback: str) -> str:
    base = email.split("@")[0] if email else fallback
    candidate = base
    counter = 1
    while store.get_user_by_username(candidate) is not None:
        candidate = f"{base}{counter}"
        counter += 1
    return candidate
