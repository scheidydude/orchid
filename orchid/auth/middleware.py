"""FastAPI auth dependencies.

Token resolution order: orchid_access cookie → Authorization: Bearer header.
JWT is verified locally (no DB hit). User record fetched from store only to
check is_active.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from orchid.auth.jwt import verify_access_token
from orchid.auth.store import UserStore
from orchid.auth.types import AuthError, User

_bearer = HTTPBearer(auto_error=False)

_default_store: UserStore | None = None


def _get_store() -> UserStore:
    global _default_store
    if _default_store is None:
        _default_store = UserStore()
    return _default_store


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
    store: UserStore = Depends(_get_store),
) -> User:
    """Validate the access token (cookie or Bearer) and return the active user.

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
    store: UserStore = Depends(_get_store),
) -> User | None:
    """Return the authenticated user, or None if no token is present.

    Never raises 401 — downstream endpoints decide whether to enforce auth.
    """
    token = _extract_token(request, credentials)
    if not token:
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
