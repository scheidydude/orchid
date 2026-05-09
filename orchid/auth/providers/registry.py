"""Provider registry — maps slug → OIDCProvider instance.

Providers can be registered directly (for testing) or loaded from the
orchid config YAML (for production).

Config format:
  auth:
    providers:
      - type: google
        client_id: "..."
        client_secret: "..."
        redirect_uri: "..."
      - type: entra
        tenant_id: "..."
        client_id: "..."
        client_secret: "..."
        redirect_uri: "..."
      - type: oidc
        name: "company-sso"
        discovery_url: "https://sso.example.com/.well-known/openid-configuration"
        client_id: "..."
        client_secret: "..."
        redirect_uri: "..."
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from orchid.auth.providers.base import OIDCProvider

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, OIDCProvider] = {}

    def register(self, provider: OIDCProvider) -> None:
        self._providers[provider.slug] = provider
        logger.debug("Registered OAuth provider: %s", provider.slug)

    def get(self, slug: str) -> OIDCProvider | None:
        return self._providers.get(slug)

    def slugs(self) -> list[str]:
        return list(self._providers.keys())

    @classmethod
    def from_config(cls, config: dict) -> "ProviderRegistry":
        """Build a registry from the orchid config dict's auth.providers list."""
        from orchid.auth.providers.entra import EntraOIDCProvider
        from orchid.auth.providers.google import GoogleOIDCProvider
        from orchid.auth.providers.oidc_generic import GenericOIDCProvider

        registry = cls()
        for entry in config.get("auth", {}).get("providers", []):
            ptype = entry.get("type", "")
            try:
                if ptype == "google":
                    registry.register(GoogleOIDCProvider(
                        client_id=entry["client_id"],
                        client_secret=entry["client_secret"],
                        redirect_uri=entry["redirect_uri"],
                    ))
                elif ptype == "entra":
                    registry.register(EntraOIDCProvider(
                        tenant_id=entry["tenant_id"],
                        client_id=entry["client_id"],
                        client_secret=entry["client_secret"],
                        redirect_uri=entry["redirect_uri"],
                    ))
                elif ptype == "oidc":
                    registry.register(GenericOIDCProvider(
                        slug=entry["name"],
                        discovery_url=entry["discovery_url"],
                        client_id=entry["client_id"],
                        client_secret=entry["client_secret"],
                        redirect_uri=entry["redirect_uri"],
                        scopes=entry.get("scopes", "openid email profile"),
                    ))
                else:
                    logger.warning("Unknown provider type %r — skipping", ptype)
            except KeyError as exc:
                logger.error("Provider config missing required field %s — skipping %r", exc, ptype)
        return registry
