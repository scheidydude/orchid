"""Microsoft Entra ID (Azure AD) OIDC provider."""
from orchid.auth.providers.oidc_generic import GenericOIDCProvider


def _discovery_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"


class EntraOIDCProvider(GenericOIDCProvider):
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        super().__init__(
            slug="entra",
            discovery_url=_discovery_url(tenant_id),
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
        self.tenant_id = tenant_id
