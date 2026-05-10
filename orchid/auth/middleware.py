"""FastAPI auth dependencies.

Token resolution order: orchid_access cookie → Authorization: Bearer header.

Two token types are supported:
- JWT access token: verified locally (no DB hit). Issued by login/refresh.
- API key ('ok_...'): verified against store. For CI/scripts/bots.

Scope enforcement (API keys only):
  API keys carry a scopes list. JWT sessions (interactive) are unrestricted.
  Use require_scope("tasks:run") to enforce a scope on an endpoint.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from orchid.auth.jwt import _API_KEY_PREFIX, verify_access_token, verify_api_key
from orchid.auth.store import get_store
from orchid.auth.types import AuthError, User

_bearer = HTTPBearer(auto_error=False)


def _get_store():
    return get_store()


def _extract_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    token = request.cookies.get("orchid_access")
    if not token and credentials:
        token = credentials.credentials
    return token


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    store=Depends(_get_store),
) -> User:
    """Validate the access token (cookie or Bearer) and return the active user.

    Accepts JWT access tokens and API keys ('ok_...' prefix).
    Raises 401 when no token is present or verification fails.
    Raises 403 when the user is inactive.
    """
    token = _extract_token(request, credentials)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token.startswith(_API_KEY_PREFIX):
        try:
            user, api_key = verify_api_key(token, store)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.api_key = api_key
        return user

    try:
        payload = verify_access_token(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = store.get_user(payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive or not found",
        )
    return user


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    store=Depends(_get_store),
) -> User | None:
    """Return the authenticated user, or None if no token is present.

    Never raises 401 — downstream endpoints decide whether to enforce auth.
    """
    token = _extract_token(request, credentials)
    if not token:
        return None

    if token.startswith(_API_KEY_PREFIX):
        try:
            user, api_key = verify_api_key(token, store)
            request.state.api_key = api_key
            return user
        except AuthError:
            return None

    try:
        payload = verify_access_token(token)
    except AuthError:
        return None

    user = store.get_user(payload["sub"])
    if user is None or not user.is_active:
        return None
    return user


def require_auth(role: str | None = None):
    """Dependency factory that enforces auth and optionally checks role.

    Usage::

        @app.get("/admin")
        async def admin(user: User = Depends(require_auth(role="admin"))):
            ...
    """
    async def _dep(current_user: User = Depends(get_current_user)) -> User:
        if role and current_user.role != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' required",
            )
        return current_user
    return _dep


def require_scope(scope: str):
    """Dependency factory that enforces a scope on API key auth.

    JWT sessions (interactive login) always pass — they are unrestricted.
    API keys must have the requested scope (or '*') in their scopes list.

    Usage::

        @app.post("/api/tasks/{id}/run")
        async def run_task(user: User = Depends(require_scope("tasks:run"))):
            ...
    """
    async def _dep(
        request: Request,
        current_user: User = Depends(get_current_user),
    ) -> User:
        api_key = getattr(request.state, "api_key", None)
        if api_key is not None:
            if "*" not in api_key.scopes and scope not in api_key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"API key missing required scope: '{scope}'",
                )
        return current_user
    return _dep
