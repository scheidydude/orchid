# Orchid auth middleware — FastAPI dependency for token-based auth.

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from orchid.auth.store import UserStore
from orchid.auth.types import User

# Singleton HTTPBearer scheme (no custom header name needed)
_bearer = HTTPBearer(auto_error=False)

# Default store path — will be overridden if the caller injects one.
_default_store: UserStore | None = None


def _get_store() -> UserStore:
    """Return the global UserStore singleton, creating it on first call."""
    global _default_store
    if _default_store is None:
        _default_store = UserStore()
    return _default_store


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    store: UserStore = Depends(_get_store),
) -> User:
    """FastAPI dependency that validates the Bearer token and returns the user.

    Raises HTTPException 401 when no token is present or the token is invalid.
    Raises HTTPException 403 when the user is inactive.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Look up the user whose token matches.
    for user in store.list_users():
        if user.token == token:
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is inactive",
                )
            return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    store: UserStore = Depends(_get_store),
) -> User | None:
    """FastAPI dependency that returns the user if a valid Bearer token is
    present, or ``None`` when no token is provided.

    Unlike ``get_current_user`` this function never raises 401 — it
    silently returns ``None`` so downstream endpoints can decide whether
    to enforce auth or treat the request as anonymous.

    Usage::

        @app.get("/api/items")
        async def list_items(current_user: User | None = Depends(get_optional_user)):
            if current_user:
                return _private_items(current_user)
            return _public_items()
    """
    if credentials is None:
        return None

    token = credentials.credentials

    for user in store.list_users():
        if user.token == token:
            if not user.is_active:
                return None
            return user

    return None


def require_auth(role: str | None = None):
    """Return a FastAPI dependency that enforces authentication and optionally
    checks the user's role.

    Usage::

        @app.get("/protected")
        async def protected(current_user: User = Depends(require_auth(role="admin"))):
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