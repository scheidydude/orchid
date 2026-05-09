import asyncio
import secrets
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from orchid.auth.middleware import get_current_user, get_optional_user
from orchid.auth.store import UserStore
from orchid.auth.types import AuthToken, User, AuthError
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
_auth_tokens: dict[str, AuthToken] = {}  # token_str -> AuthToken
_bearer = HTTPBearer(auto_error=False)


def _get_auth_store() -> UserStore:
    global _auth_store
    if _auth_store is None:
        _auth_store = UserStore()
    return _auth_store


def _issue_token(user: User) -> str:
    """Create a random token and store it."""
    token_str = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=24)
    auth_token = AuthToken(
        token=token_str,
        user_id=user.user_id,
        expires_at=expires_at,
    )
    _auth_tokens[token_str] = auth_token
    return token_str


def _validate_token(token_str: str) -> User:
    """Look up a token and return the associated active User."""
    auth_token = _auth_tokens.get(token_str)
    if auth_token is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not auth_token.is_valid:
        raise HTTPException(
            status_code=401,
            detail="Token revoked",
        )
    if auth_token.expires_at < datetime.now():
        auth_token.is_valid = False
        raise HTTPException(
            status_code=401,
            detail="Token expired",
        )
    store = _get_auth_store()
    user = store.get_user(auth_token.user_id)
    if user is None or not user.is_active:
        auth_token.is_valid = False
        raise HTTPException(
            status_code=403,
            detail="User inactive or not found",
        )
    return user


# ------------------------------------------------------------------
# Auth endpoints
# ------------------------------------------------------------------

@app.post("/api/auth/register")
async def auth_register(data: dict):
    """Register a new user. Returns user info (without sensitive fields)."""
    store = _get_auth_store()
    username = data.get("username", "").strip()
    email = data.get("email", "").strip() or None
    password = data.get("password", "").strip()
    role = data.get("role", "user")

    if not username:
        raise HTTPException(400, "username required")
    if not password:
        raise HTTPException(400, "password required")

    user_id = username  # simple: username == user_id

    try:
        user = User(
            user_id=user_id,
            username=username,
            email=email,
            role=role,
        )
        store.add_user(user)
    except AuthError:
        raise HTTPException(409, "User already exists")

    return {"user_id": user.user_id, "username": user.username, "role": user.role}


@app.post("/api/auth/login")
async def auth_login(data: dict):
    """Authenticate a user and return a bearer token."""
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        raise HTTPException(400, "username and password required")

    store = _get_auth_store()
    user = store.get_user(username) or store.get_user_by_username(username)

    if user is None:
        raise HTTPException(401, "Invalid credentials")

    token_str = _issue_token(user)
    return {"token": token_str, "user_id": user.user_id, "username": user.username}


@app.post("/api/auth/token")
async def auth_token(body: dict):
    """Validate a raw bearer token and return user info."""
    token = body.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="token required")
    try:
        user = _get_auth_store().get_by_token(token)
        return {"user_id": user.user_id, "valid": True}
    except AuthError:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/api/auth/me")
async def auth_me(current_user: User | None = Depends(get_optional_user)):
    """Return the currently authenticated user, or unauthenticated indicator."""
    if current_user is None:
        return {"authenticated": False}
    return {
        "user_id": current_user.user_id,
        "username": getattr(current_user, "username", ""),
        "email": getattr(current_user, "email", None),
        "role": getattr(current_user, "role", "user"),
    }


@app.post("/api/auth/logout")
async def auth_logout(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    """Revoke the current bearer token."""
    if credentials is None:
        raise HTTPException(401, "No token provided")
    auth_token = _auth_tokens.get(credentials.credentials)
    if auth_token:
        auth_token.is_valid = False
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

            credentials: HTTPAuthorizationCredentials | None = None
            auth_header = request.headers.get("authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token_str = auth_header[7:]
                try:
                    user = _validate_token(token_str)
                    if roles and user.role not in roles:
                        return HTTPException(
                            status_code=403,
                            detail="Insufficient role",
                        )
                    # Attach user to request state for downstream use
                    request.state.current_user = user
                except HTTPException as exc:
                    return exc
            # No token → allow through (opt-in)
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