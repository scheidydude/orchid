import asyncio
import secrets
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from orchid.auth.jwt import (
    hash_password,
    issue_access_token,
    issue_api_key,
    issue_refresh_token,
    verify_access_token,
    verify_password,
    verify_refresh_token,
)
from orchid.auth.middleware import get_current_user, get_optional_user, require_scope
from orchid.auth.providers.registry import ProviderRegistry
from orchid.auth.store import UserStore
from orchid.auth.types import AuthError, User
from orchid.registry import ProjectRegistry

from orchid.planning import PlanningSession
from orchid.runner import BackgroundRunner

app = FastAPI()

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

registry = ProjectRegistry()
runner = BackgroundRunner()

# ------------------------------------------------------------------
# Auth layer
# ------------------------------------------------------------------

_auth_store: UserStore | None = None
_bearer = HTTPBearer(auto_error=False)

_COOKIE_ACCESS = "orchid_access"
_COOKIE_REFRESH = "orchid_refresh"
_COOKIE_OPTS: dict = {"httponly": True, "samesite": "strict"}

# OAuth state: token → {provider_slug, expires_at}  (10-minute TTL)
_oauth_states: dict[str, dict] = {}

# Provider registry — populated at startup or via register_oauth_provider()
_provider_registry = ProviderRegistry()


def register_oauth_provider(provider) -> None:
    """Register an OIDC provider at runtime (called by serve.py or tests)."""
    _provider_registry.register(provider)


def _get_auth_store() -> UserStore:
    global _auth_store
    if _auth_store is None:
        _auth_store = UserStore()
    return _auth_store


def _set_auth_cookies(response: Response, access_token: str, refresh_raw: str) -> None:
    response.set_cookie(_COOKIE_ACCESS, access_token, max_age=900, **_COOKIE_OPTS)
    response.set_cookie(_COOKIE_REFRESH, refresh_raw, max_age=2_592_000, **_COOKIE_OPTS)


def _validate_token(token_str: str) -> User:
    """JWT-based token validation used by auth_guard middleware."""
    try:
        payload = verify_access_token(token_str)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"})
    store = _get_auth_store()
    user = store.get_user(payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive or not found")
    return user


# ------------------------------------------------------------------
# Auth endpoints
# ------------------------------------------------------------------

@app.post("/api/auth/register")
async def auth_register(data: dict):
    """Register a new user. Returns user info (no sensitive fields)."""
    store = _get_auth_store()
    username = data.get("username", "").strip()
    email = data.get("email", "").strip() or None
    password = data.get("password", "").strip()
    role = data.get("role", "user")

    if not username:
        raise HTTPException(400, "username required")
    if not password:
        raise HTTPException(400, "password required")
    if role not in ("user", "admin", "readonly"):
        raise HTTPException(400, "role must be user, admin, or readonly")

    try:
        user = User(
            user_id=username,
            username=username,
            email=email,
            role=role,
            password_hash=hash_password(password),
        )
        store.add_user(user)
    except AuthError:
        raise HTTPException(409, "User already exists")

    return {"user_id": user.user_id, "username": user.username, "role": user.role}


@app.post("/api/auth/login")
async def auth_login(data: dict, response: Response):
    """Authenticate with username+password. Sets HttpOnly cookies and returns user info."""
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        raise HTTPException(400, "username and password required")

    store = _get_auth_store()
    user = store.get_user(username) or store.get_user_by_username(username)

    if user is None or not user.is_active:
        raise HTTPException(401, "Invalid credentials")
    if not user.password_hash or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    access_token = issue_access_token(user)
    refresh_raw, rt = issue_refresh_token(user)
    store.store_refresh_token(rt)
    _set_auth_cookies(response, access_token, refresh_raw)

    return {
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role,
        "access_token": access_token,
    }


@app.post("/api/auth/refresh")
async def auth_refresh(request: Request, response: Response):
    """Rotate a refresh token. Issues new access + refresh tokens (old refresh invalidated)."""
    raw = request.cookies.get(_COOKIE_REFRESH)
    if not raw:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        raw = body.get("refresh_token", "")
    if not raw:
        raise HTTPException(401, "No refresh token")

    store = _get_auth_store()
    try:
        rt = verify_refresh_token(raw, store)
    except AuthError as exc:
        raise HTTPException(401, str(exc))

    store.revoke_refresh_token(rt.token_id)

    user = store.get_user(rt.user_id)
    if user is None or not user.is_active:
        raise HTTPException(403, "User inactive or not found")

    new_access = issue_access_token(user)
    new_raw, new_rt = issue_refresh_token(user)
    store.store_refresh_token(new_rt)
    _set_auth_cookies(response, new_access, new_raw)

    return {"user_id": user.user_id, "username": user.username, "access_token": new_access}


@app.post("/api/auth/token")
async def auth_token(body: dict):
    """Verify a JWT access token and return user info."""
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="token required")
    try:
        payload = verify_access_token(token)
        return {"user_id": payload["sub"], "valid": True}
    except AuthError:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/api/auth/me")
async def auth_me(current_user: User | None = Depends(get_optional_user)):
    """Return the currently authenticated user, or unauthenticated indicator."""
    if current_user is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user_id": current_user.user_id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
    }


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    """Revoke the refresh token and clear auth cookies."""
    raw = request.cookies.get(_COOKIE_REFRESH)
    if raw:
        store = _get_auth_store()
        try:
            rt = verify_refresh_token(raw, store)
            store.revoke_refresh_token(rt.token_id)
        except AuthError:
            pass  # token already invalid — still clear cookies
    response.delete_cookie(_COOKIE_ACCESS)
    response.delete_cookie(_COOKIE_REFRESH)
    return {"ok": True}


@app.get("/api/auth/users")
async def auth_list_users(current_user: User = Depends(get_current_user)):
    """List all users (admin-only)."""
    if current_user.role != "admin":
        raise HTTPException(403, "Admin role required")
    store = _get_auth_store()
    users = store.list_users()
    return {
        "users": [
            {
                "user_id": u.user_id,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
            }
            for u in users
        ]
    }


# ------------------------------------------------------------------
# API key endpoints (Phase 2)
# ------------------------------------------------------------------

@app.post("/api/auth/apikeys")
async def create_api_key(data: dict, current_user: User = Depends(get_current_user)):
    """Create an API key for programmatic access. Secret returned once — store it."""
    name = data.get("name", "").strip()
    scopes = data.get("scopes", [])
    expires_days = data.get("expires_days")

    if not name:
        raise HTTPException(400, "name required")
    if not isinstance(scopes, list):
        raise HTTPException(400, "scopes must be a list of strings")
    if not all(isinstance(s, str) for s in scopes):
        raise HTTPException(400, "scopes must be a list of strings")

    expires_at: Optional[datetime] = None
    if expires_days is not None:
        try:
            expires_at = datetime.now(timezone.utc) + timedelta(days=int(expires_days))
        except (TypeError, ValueError):
            raise HTTPException(400, "expires_days must be an integer")

    raw, key = issue_api_key(current_user, name, scopes, expires_at)
    _get_auth_store().store_api_key(key)

    return {
        "key_id": key.key_id,
        "name": key.name,
        "scopes": key.scopes,
        "secret": raw,
        "created_at": key.created_at,
        "expires_at": key.expires_at,
    }


@app.get("/api/auth/apikeys")
async def list_api_keys(current_user: User = Depends(get_current_user)):
    """List the current user's API keys. Secrets are never returned."""
    keys = _get_auth_store().list_api_keys(current_user.user_id)
    return {
        "api_keys": [
            {
                "key_id": k.key_id,
                "name": k.name,
                "scopes": k.scopes,
                "created_at": k.created_at,
                "last_used": k.last_used,
                "expires_at": k.expires_at,
                "is_active": k.is_active,
            }
            for k in keys
        ]
    }


@app.delete("/api/auth/apikeys/{key_id}")
async def revoke_api_key(key_id: str, current_user: User = Depends(get_current_user)):
    """Revoke an API key. Admins can revoke any key; users can only revoke their own."""
    store = _get_auth_store()
    key = store.get_api_key(key_id)
    if key is None:
        raise HTTPException(404, "API key not found")
    if key.user_id != current_user.user_id and current_user.role != "admin":
        raise HTTPException(403, "Cannot revoke another user's API key")
    store.revoke_api_key(key_id)
    return {"ok": True}


# ------------------------------------------------------------------
# ------------------------------------------------------------------
# OAuth 2.0 / OIDC endpoints (Phase 3)
# ------------------------------------------------------------------

def _create_oauth_state(provider_slug: str) -> str:
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "provider": provider_slug,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    return state


def _consume_oauth_state(state: str) -> dict:
    data = _oauth_states.pop(state, None)
    if data is None:
        raise AuthError("Invalid or expired OAuth state")
    if data["expires_at"] < datetime.now(timezone.utc):
        raise AuthError("OAuth state expired")
    return data


@app.get("/api/auth/oauth/{provider_slug}/start")
async def oauth_start(provider_slug: str):
    """Redirect the browser to the provider's authorization page."""
    provider = _provider_registry.get(provider_slug)
    if provider is None:
        raise HTTPException(404, f"OAuth provider '{provider_slug}' not configured")
    state = _create_oauth_state(provider_slug)
    url = await provider.authorization_url(state)
    return RedirectResponse(url, status_code=302)


async def _resolve_oauth_callback(
    provider_slug: str,
    code: str,
    state: str,
) -> tuple[str, str]:
    """Validate state, exchange code, issue Orchid tokens.

    Returns (access_token, refresh_raw).
    """
    try:
        state_data = _consume_oauth_state(state)
    except AuthError as exc:
        raise HTTPException(400, str(exc))

    if state_data["provider"] != provider_slug:
        raise HTTPException(400, "OAuth state provider mismatch")

    provider = _provider_registry.get(provider_slug)
    if provider is None:
        raise HTTPException(404, f"OAuth provider '{provider_slug}' not configured")

    store = _get_auth_store()
    try:
        user, _oa = await provider.handle_callback(code, store)
    except AuthError as exc:
        raise HTTPException(401, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"OAuth provider error: {exc}")

    access_token = issue_access_token(user)
    refresh_raw, rt = issue_refresh_token(user)
    store.store_refresh_token(rt)
    return access_token, refresh_raw


@app.get("/api/auth/oauth/{provider_slug}/callback")
async def oauth_callback_get(provider_slug: str, request: Request):
    """Handle OAuth callback via GET redirect (standard web flow).

    Sets HttpOnly cookies and redirects browser to /?oauth=success.
    """
    error = request.query_params.get("error", "")
    if error:
        raise HTTPException(400, f"OAuth provider returned error: {error}")
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    if not code or not state:
        raise HTTPException(400, "Missing 'code' or 'state' parameter")

    access_token, refresh_raw = await _resolve_oauth_callback(provider_slug, code, state)
    redirect = RedirectResponse("/?oauth=success", status_code=302)
    _set_auth_cookies(redirect, access_token, refresh_raw)
    return redirect


@app.post("/api/auth/oauth/{provider_slug}/callback")
async def oauth_callback_post(provider_slug: str, request: Request, response: Response):
    """Handle OAuth callback via POST (some providers; also mobile token exchange)."""
    try:
        body = await request.json()
        code = body.get("code", "")
        state = body.get("state", "")
    except Exception:
        form = await request.form()
        code = form.get("code", "")
        state = form.get("state", "")
    if not code or not state:
        raise HTTPException(400, "Missing 'code' or 'state' parameter")
    access_token, refresh_raw = await _resolve_oauth_callback(provider_slug, code, state)
    _set_auth_cookies(response, access_token, refresh_raw)
    return {"access_token": access_token}


@app.get("/api/auth/oauth/providers")
async def list_oauth_providers():
    """List configured OAuth providers (for the login UI to show SSO buttons)."""
    return {"providers": _provider_registry.slugs()}


# Optional auth guard decorator
# ------------------------------------------------------------------

def auth_guard(
    app_instance: FastAPI,
    roles: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> None:
    """Attach an optional auth guard to *app_instance*.

    Every incoming request is checked for a Bearer token.  If a token is
    present it is validated; if validation fails the request is rejected
    with 401/403.  If no token is present the request is allowed to
    proceed (opt-in auth).

    Args:
        app_instance: The FastAPI application to protect.
        roles: If given, only users whose role is in this list may access
            protected endpoints.  ``None`` means any authenticated user.
        exclude_paths: URL prefixes that should never be guarded
            (e.g. ``["/api/auth", "/static"]``).
    """
    from fastapi.middleware.base import BaseHTTPMiddleware

    exclude = set(exclude_paths or [])

    class _AuthGuardMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if any(path.startswith(ep) for ep in exclude):
                return await call_next(request)

            token_str = request.cookies.get(_COOKIE_ACCESS)
            if not token_str:
                auth_header = request.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    token_str = auth_header[7:]
            if token_str:
                try:
                    user = _validate_token(token_str)
                    if roles and user.role not in roles:
                        from fastapi.responses import JSONResponse
                        return JSONResponse({"detail": "Insufficient role"}, status_code=403)
                    request.state.current_user = user
                except HTTPException as exc:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            # No token → allow through (opt-in auth)
            return await call_next(request)

    app_instance.add_middleware(_AuthGuardMiddleware)


# ------------------------------------------------------------------
# Core application objects
# ------------------------------------------------------------------

class NDJSONStreamEmitter:
    """Thread-safe in-memory NDJSON emitter that pushes events into a deque.

    Used by the /api/projects/{project_id}/stream endpoint to collect
    session-level and task-level events emitted by the orchestrator / runner.
    """

    def __init__(self) -> None:
        self._buffer: deque[str] = deque()
        self._closed = False

    def emit(self, event: Any) -> None:
        """Append one NDJSON line to the buffer."""
        if self._closed:
            return
        json_line = event.to_json()
        self._buffer.append(json_line)

    def close(self) -> None:
        self._closed = True

    def drain(self) -> list[str]:
        """Return all buffered lines and clear the buffer."""
        items = list(self._buffer)
        self._buffer.clear()
        return items

    @property
    def is_closed(self) -> bool:
        return self._closed


# Global mapping from project_path -> stream emitter
_stream_emitters: dict[str, NDJSONStreamEmitter] = {}


@app.get("/")
async def root():
    return FileResponse(str(static_dir / "index.html"))


@app.get("/api/projects")
async def list_projects():
    projects = registry.list_projects()
    return {"projects": projects}


@app.post("/api/projects")
async def add_project(data: dict):
    path = data.get("path", "").strip()
    if not path:
        raise HTTPException(400, "path required")
    project = registry.add_project(path)
    return project


@app.delete("/api/projects/{project_id}")
async def remove_project(project_id: str):
    registry.remove_project(project_id)
    return {"ok": True}


@app.get("/api/projects/{project_id}/tasks")
async def get_tasks(project_id: str):
    project = _get_project(project_id)
    tasks_file = Path(project['path']) / 'tasks.md'
    if not tasks_file.exists():
        return {"tasks": []}
    from orchid.tasks import parse_tasks
    tasks = parse_tasks(tasks_file.read_text())
    return {"tasks": tasks}


@app.get("/api/projects/{project_id}/artifacts")
async def get_artifacts(project_id: str):
    project = _get_project(project_id)
    project_path = Path(project['path'])
    artifacts = []
    for name in ['REQUIREMENTS.md', 'ARCHITECTURE.md', 'tasks.md']:
        filepath = project_path / name
        if filepath.exists():
            artifacts.append({
                'name': name,
                'content': filepath.read_text(),
                'modified': filepath.stat().st_mtime,
            })
    return {"artifacts": artifacts}


@app.post("/api/projects/{project_id}/run")
async def run_project(project_id: str):
    project = _get_project(project_id)
    runner.start(project['path'])
    return {"ok": True}


@app.delete("/api/projects/{project_id}/run")
async def stop_run(project_id: str):
    project = _get_project(project_id)
    runner.stop(project['path'])
    return {"ok": True}


@app.get("/api/projects/{project_id}/status")
async def get_status(project_id: str):
    project = _get_project(project_id)
    status = runner.get_status(project['path'])
    return status


async def _event_generator(project_id: str, project_path: str) -> Any:
    """Drain the emitter buffer and yield NDJSON lines."""
    project_name = project_id  # used as session_id

    # Yield session_start immediately
    from orchid.output.events import SessionStartEvent
    yield SessionStartEvent(
        session_id=project_name,
        project=project_path,
        mode="auto",
    ).to_json() + "\n"

    try:
        while True:
            lines = _stream_emitters.get(project_path, NDJSONStreamEmitter()).drain()
            for line in lines:
                yield line + "\n"

            # Check if the run has finished
            status = runner.get_status(project_path)
            if not status.get("running", False):
                break

            await asyncio.sleep(0.2)
    finally:
        # Emit session_end when the generator closes
        from orchid.output.events import SessionEndEvent
        # Gather final counts from runner
        final_status = runner.get_status(project_path)
        duration_s = 0.0  # approximate -- exact duration tracked by runner
        yield SessionEndEvent(
            session_id=project_name,
            task_count=final_status.get("tasks_done", 0),
            duration_s=duration_s,
        ).to_json() + "\n"
        if project_path in _stream_emitters:
            _stream_emitters[project_path].close()


@app.get("/api/projects/{project_id}/stream")
async def stream_events(project_id: str):
    """NDJSON streaming endpoint.

    Yields session-level and task-level events emitted during an auto-run.
    Events are buffered by an ``NDJSONStreamEmitter`` that the runner
    attaches to the orchestrator.  The client polls via SSE-style
    long-polling: the generator drains the buffer every 200 ms until
    the run finishes.
    """
    project = _get_project(project_id)
    project_path = project['path']

    # Register the emitter for this project if not already present
    if project_path not in _stream_emitters:
        _stream_emitters[project_path] = NDJSONStreamEmitter()

    return StreamingResponse(
        _event_generator(project_id, project_path),
        media_type="application/x-ndjson",
    )


@app.websocket("/ws/planning/{project_id}")
async def planning_ws(websocket: WebSocket, project_id: str):
    await websocket.accept()

    project_path = None
    for p in registry.list_projects():
        if p['id'] == project_id:
            project_path = p['path']
            break

    if not project_path:
        await websocket.send_json({"type": "error", "content": "Project not found"})
        await websocket.close()
        return

    session = PlanningSession(project_path)

    # Send history
    history = session.get_history()
    if history:
        await websocket.send_json({"type": "history", "messages": history})

    async def send_status(status_text: str):
        """Stream status updates to the client during artifact generation."""
        if status_text.startswith('artifacts_ready:'):
            filenames = status_text[len('artifacts_ready:'):].split(',')
            await websocket.send_json({
                "type": "artifacts_ready",
                "files": filenames,
            })
        else:
            await websocket.send_json({
                "type": "status",
                "content": status_text,
            })

    try:
        while True:
            data = await websocket.receive_json()
            if data.get('type') == 'message':
                user_msg = data.get('content', '')
                response = await session.chat(user_msg, status_callback=send_status)
                await websocket.send_json({"type": "message", "content": response})
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/logs/{project_id}")
async def logs_ws(websocket: WebSocket, project_id: str):
    await websocket.accept()
    project = _get_project_or_none(project_id)
    if not project:
        await websocket.close()
        return

    last_size = 0
    try:
        while True:
            log_file = Path(project['path']) / '.orchid' / 'current.log'
            if log_file.exists():
                size = log_file.stat().st_size
                if size > last_size:
                    with open(log_file) as f:
                        f.seek(last_size)
                        new_content = f.read()
                    last_size = size
                    await websocket.send_json({"type": "log", "content": new_content})
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass


def _get_project(project_id: str):
    for p in registry.list_projects():
        if p['id'] == project_id:
            return p
    raise HTTPException(404, "Project not found")


def _get_project_or_none(project_id: str):
    for p in registry.list_projects():
        if p['id'] == project_id:
            return p
    return None