"""Generic OIDC provider — works with any standards-compliant OIDC server.

Uses the discovery document (.well-known/openid-configuration) to find
authorization, token, and userinfo endpoints. Google and Entra ID are
subclasses that pre-set the discovery URL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx

from orchid.auth.providers.base import OIDCProvider, link_or_create_user
from orchid.auth.types import AuthError, OAuthAccount, User

if TYPE_CHECKING:
    from orchid.auth.store import UserStore


class GenericOIDCProvider(OIDCProvider):
    """OIDC provider backed by a discovery URL."""

    def __init__(
        self,
        slug: str,
        discovery_url: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: str = "openid email profile",
    ) -> None:
        self._slug = slug
        self.discovery_url = discovery_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self._metadata: dict | None = None

    @property
    def slug(self) -> str:
        return self._slug

    async def _get_metadata(self) -> dict:
        if self._metadata is None:
            async with httpx.AsyncClient() as client:
                r = await client.get(self.discovery_url, timeout=10)
                r.raise_for_status()
                self._metadata = r.json()
        return self._metadata

    async def authorization_url(self, state: str) -> str:
        meta = await self._get_metadata()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
        }
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    async def handle_callback(
        self,
        code: str,
        store: "UserStore",
    ) -> tuple[User, OAuthAccount]:
        meta = await self._get_metadata()
        tokens = await self._exchange_code(meta["token_endpoint"], code)
        userinfo = await self._fetch_userinfo(meta["userinfo_endpoint"], tokens["access_token"])

        provider_user_id = userinfo.get("sub", "")
        email = userinfo.get("email", "")
        if not provider_user_id:
            raise AuthError("OIDC userinfo missing 'sub' claim")

        expires_at = None
        if "expires_in" in tokens:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))

        return link_or_create_user(
            store=store,
            provider=self.slug,
            provider_user_id=provider_user_id,
            email=email,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=expires_at,
        )

    async def _exchange_code(self, token_endpoint: str, code: str) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=15,
            )
            r.raise_for_status()
            tokens = r.json()
        if "access_token" not in tokens:
            raise AuthError(f"Token exchange failed: {tokens.get('error', 'unknown')}")
        return tokens

    async def _fetch_userinfo(self, userinfo_endpoint: str, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
