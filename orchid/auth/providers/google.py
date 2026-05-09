"""Google OIDC provider."""
from orchid.auth.providers.oidc_generic import GenericOIDCProvider

_GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


class GoogleOIDCProvider(GenericOIDCProvider):
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        super().__init__(
            slug="google",
            discovery_url=_GOOGLE_DISCOVERY_URL,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
