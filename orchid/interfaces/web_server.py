"""FastAPI web server for Orchid.

REST API + WebSocket for real-time agent events.
Serves React frontend from web_ui/dist/.

Architecture (D0027):
- FastAPI with uvicorn for async HTTP + WebSocket
- ConnectionManager per project: thread-safe broadcast to WebSocket clients
- _WebProjectRunner: runs orchestrator in thread pool, emits events to WebSocket

Auto-discovery (D0033):
- create_app() accepts optional watch_dirs for ProjectDiscovery
- ProjectDiscovery watches for .orchid.yaml created/deleted
- New projects are registered dynamically; all WS clients are notified

Usage (via CLI):
    orchid web --project <path> [--port 7842] [--host 0.0.0.0]
    orchid serve --watch-dir ~/LocalAI [--port 7842]
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import json
import logging
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

try:
    from orchid.auth.audit import AuditAction
    from orchid.auth.jwt import (
        hash_password,
        issue_access_token,
        issue_api_key,
        issue_refresh_token,
        verify_access_token,
        verify_password,
        verify_refresh_token,
    )
    from orchid.auth.middleware import get_current_user, get_optional_user, require_auth
    from orchid.auth.types import AuthError, User
    _AUTH_AVAILABLE = True
except ImportError:
    _AUTH_AVAILABLE = False

_DIST_DIR = Path(__file__).parent / "web_ui" / "dist"
_PORTAL_DIST_DIR = Path(__file__).parent / "portal" / "dist"
_ADMIN_DIST_DIR = Path(__file__).parent / "admin" / "dist"

# ── Module-level state ─────────────────────────────────────────────────────────

_projects: dict[str, str] = {}           # project_id → absolute project path
_managers: dict[str, ConnectionManager] = {}
_runners: dict[str, _WebProjectRunner] = {}
_main_loop: asyncio.AbstractEventLoop | None = None
_state_lock = threading.Lock()           # protects _projects/_managers/_runners

# Optional discovery instance (set by create_app when watch_dirs provided)
_discovery: Any | None = None
_agent_manager: Any | None = None
_central_bot_manager: Any | None = None
_cron_engine: Any | None = None

# ── Auth module-level state ────────────────────────────────────────────────────

_bearer: Any = None
_COOKIE_ACCESS = "orchid_access"
_COOKIE_REFRESH = "orchid_refresh"
_COOKIE_OPTS: dict = {"httponly": True, "samesite": "strict"}
_oauth_states: dict[str, dict] = {}
_provider_registry: Any = None
_audit_store: Any | None = None


def _init_auth_globals() -> None:
    global _bearer, _provider_registry
    if _AUTH_AVAILABLE and _bearer is None:
        from fastapi.security import HTTPBearer as _HTTPBearer

        from orchid.auth.providers.registry import ProviderRegistry as _PR
        _bearer = _HTTPBearer(auto_error=False)
        _provider_registry = _PR()


def _get_auth_store() -> Any:
    from orchid.auth.store import get_store
    return get_store()


def _get_audit_store() -> Any:
    global _audit_store
    if _audit_store is None:
        from orchid.auth.audit import AuditStore as _AS
        _audit_store = _AS()
    return _audit_store


def _log_audit(user: Any, action: str, resource: str, result: str,
               request: Any = None, detail: str = "") -> None:
    try:
        from orchid.auth.audit import make_event as _me
        ip = ""
        if request and request.client:
            ip = request.client.host
        user_id = user.user_id if user else "anonymous"
        event = _me(user_id=user_id, action=action, resource=resource,
                    result=result, ip=ip, detail=detail)
        _get_audit_store().log(event)
    except Exception as exc:
        logger.warning("Audit log failed: %s", exc)


def _check_project_access(user: Any, project_id: str) -> None:
    if user.role == "admin":
        return
    if user.projects and project_id not in user.projects:
        _log_audit(user, AuditAction.PROJECT_ACCESS_DENIED, project_id, "denied")
        raise HTTPException(403, f"Access to project '{project_id}' denied")


def _set_auth_cookies(response: Any, access_token: str, refresh_raw: str) -> None:
    response.set_cookie(_COOKIE_ACCESS, access_token, max_age=900, **_COOKIE_OPTS)
    response.set_cookie(_COOKIE_REFRESH, refresh_raw, max_age=2_592_000, **_COOKIE_OPTS)


def _create_oauth_state(provider_slug: str, code_challenge: str = "",
                        code_challenge_method: str = "S256") -> str:
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "provider": provider_slug,
        "expires_at": datetime.now(UTC) + timedelta(minutes=10),
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }
    return state


def _consume_oauth_state(state: str) -> dict:
    from orchid.auth.types import AuthError as _AE
    data = _oauth_states.pop(state, None)
    if data is None:
        raise _AE("Invalid or expired OAuth state")
    if data["expires_at"] < datetime.now(UTC):
        raise _AE("OAuth state expired")
    return data


def _verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


def _project_id(path: str) -> str:
    """Derive a stable project ID from a path (basename, deduplicated)."""
    return Path(path).name


def _setup_projects(project_paths: list[str]) -> None:
    """Register projects and create per-project state objects."""
    global _projects, _managers, _runners
    seen: dict[str, int] = {}
    for path in project_paths:
        base = Path(path).name
        if base in seen:
            seen[base] += 1
            pid = f"{base}_{seen[base]}"
        else:
            seen[base] = 0
            pid = base
        _projects[pid] = str(Path(path).resolve())
        _managers[pid] = ConnectionManager()
        _runners[pid] = _WebProjectRunner(_projects[pid], _managers[pid])


def _register_project(path: str) -> str | None:
    """Register a newly discovered project. Returns project_id or None if already registered."""
    resolved = str(Path(path).resolve())
    with _state_lock:
        # Check if already registered by path
        for pid, existing_path in _projects.items():
            if existing_path == resolved:
                return None  # already known
        # Derive unique ID
        base = Path(resolved).name
        pid = base
        suffix = 1
        while pid in _projects:
            pid = f"{base}_{suffix}"
            suffix += 1
        _projects[pid] = resolved
        _managers[pid] = ConnectionManager()
        _runners[pid] = _WebProjectRunner(resolved, _managers[pid])
    logger.info("Registered new project: %s → %s", pid, resolved)
    return pid


def _unregister_project(path: str) -> str | None:
    """Unregister a removed project. Returns project_id or None if not found."""
    resolved = str(Path(path).resolve())
    with _state_lock:
        pid_to_remove = None
        for pid, existing_path in list(_projects.items()):
            if existing_path == resolved:
                pid_to_remove = pid
                break
        if pid_to_remove is None:
            return None
        runner = _runners.pop(pid_to_remove, None)
        if runner:
            runner.stop()
        _managers.pop(pid_to_remove, None)
        del _projects[pid_to_remove]
    logger.info("Unregistered project: %s", pid_to_remove)
    return pid_to_remove


def _broadcast_to_all(data: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
    """Broadcast a message to all connected WebSocket clients across all projects."""
    with _state_lock:
        managers = list(_managers.values())
    for mgr in managers:
        mgr.broadcast_sync(data, loop)


# ── Connection manager ────────────────────────────────────────────────────────

class ConnectionManager:
    """Thread-safe WebSocket connection pool for one project."""

    def __init__(self) -> None:
        self._connections: list[Any] = []
        self._lock = threading.Lock()

    async def connect(self, ws: Any) -> None:
        await ws.accept()
        with self._lock:
            self._connections.append(ws)

    def remove(self, ws: Any) -> None:
        with self._lock:
            self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, data: dict[str, Any]) -> None:
        from orchid import config as _cfg
        _timeout = float(_cfg.get("web.ws_send_timeout", 5.0))
        with self._lock:
            conns = list(self._connections)
        dead = []
        for ws in conns:
            try:
                await asyncio.wait_for(ws.send_json(data), timeout=_timeout)
            except Exception:
                dead.append(ws)
        for d in dead:
            self.remove(d)

    def broadcast_sync(self, data: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
        """Thread-safe broadcast from background threads."""
        if not loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.broadcast(data), loop)


# ── Project runner ────────────────────────────────────────────────────────────

class _WebProjectRunner:
    """Runs an Orchid orchestrator in a thread pool; emits events via ConnectionManager."""

    def __init__(self, project_path: str, manager: ConnectionManager) -> None:
        self.project_path = project_path
        self._manager = manager
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orchid-web")
        self._future: Any | None = None
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self.current_task: str = ""
        self.tasks_done: int = 0
        self.run_id: str = ""

    def is_running(self) -> bool:
        with self._lock:
            return self._future is not None and not self._future.done()

    def start_single_task(
        self,
        loop: asyncio.AbstractEventLoop,
        task_id: str,
        code_model: str | None = None,
    ) -> str:
        with self._lock:
            if self.is_running():
                return ""
            rid = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            self.run_id = rid
            self._cancel.clear()
            self.current_task = task_id
            self.tasks_done = 0
            self._future = self._executor.submit(
                self._run_single_task_sync, loop, task_id, code_model
            )
            return rid

    def _run_single_task_sync(
        self,
        loop: asyncio.AbstractEventLoop,
        task_id: str,
        code_model: str | None,
    ) -> None:
        from orchid.orchestrator import Orchestrator
        from orchid.session import Session

        try:
            session = Session(project_dir=self.project_path)
            session.load()
            task = next((t for t in session.tasks if t.id == task_id), None)
            if task is None:
                self._emit(loop, "error", {"message": f"Task {task_id} not found"})
                return

            self._emit(loop, "session_start", {"project": session.project_name, "pending": 1})
            self._emit(loop, "task_start", {
                "task_id": task.id, "title": task.title, "remaining": 1,
            })

            orch = Orchestrator(session, cli_model_override=code_model)

            def _stream_cb(data: dict[str, Any]) -> None:
                if data.get("event") == "task_progress":
                    self._emit(loop, "task_progress", data)

            orch.stream_callback = _stream_cb
            result = orch._execute_task(task)
            session.save()
            result_text = str(result.get("result", "")) if result else ""
            with self._lock:
                self.tasks_done = 1
            self._emit(loop, "task_complete", {
                "task_id": task.id,
                "result_snippet": result_text[:200],
                "done_so_far": 1,
            })
            self._emit(loop, "session_complete", {"done": [task.id], "failed": []})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Web runner single task %s failed", task_id)
            self._emit(loop, "task_failed", {"task_id": task_id, "error": str(exc)})
            self._emit(loop, "error", {"message": str(exc)})
        finally:
            with self._lock:
                self.current_task = ""

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        mode: str = "auto",
        code_model: str | None = None,
    ) -> str:
        with self._lock:
            if self.is_running():
                return ""
            rid = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            self.run_id = rid
            self._cancel.clear()
            self.current_task = ""
            self.tasks_done = 0
            self._future = self._executor.submit(self._run_sync, loop, mode, code_model)
            return rid

    def stop(self) -> None:
        self._cancel.set()

    def inject(self, text: str) -> None:
        queue_path = Path(self.project_path) / ".orchid" / "inject.queue"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        with open(queue_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    def _emit(self, loop: asyncio.AbstractEventLoop, event_type: str, data: dict[str, Any]) -> None:
        self._manager.broadcast_sync({"type": event_type, "data": data}, loop)

    def _run_sync(
        self,
        loop: asyncio.AbstractEventLoop,
        mode: str,
        code_model: str | None,
    ) -> None:
        from orchid.memory.state import TaskStatus
        from orchid.orchestrator import Orchestrator
        from orchid.session import Session

        done_ids: list[str] = []
        failed_ids: list[str] = []

        try:
            session = Session(project_dir=self.project_path)
            session.load()
            pending_count = sum(1 for t in session.tasks if t.status == TaskStatus.TODO)
            self._emit(loop, "session_start", {
                "project": session.project_name,
                "pending": pending_count,
            })

            orch = Orchestrator(session, cli_model_override=code_model)

            def _stream_cb(data: dict[str, Any]) -> None:
                if data.get("event") == "task_progress":
                    self._emit(loop, "task_progress", data)

            orch.stream_callback = _stream_cb

            while not self._cancel.is_set():
                task = session.next_task()
                if task is None:
                    break

                with self._lock:
                    self.current_task = task.id
                remaining = sum(1 for t in session.tasks if t.status == TaskStatus.TODO)
                self._emit(loop, "task_start", {
                    "task_id": task.id,
                    "title": task.title,
                    "remaining": remaining,
                })

                try:
                    result = orch._execute_task(task)
                    session.save()
                    result_text = str(result.get("result", "")) if result else ""
                    done_ids.append(task.id)
                    with self._lock:
                        self.tasks_done += 1
                    self._emit(loop, "task_complete", {
                        "task_id": task.id,
                        "result_snippet": result_text[:200],
                        "done_so_far": len(done_ids),
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Web runner task %s failed", task.id)
                    failed_ids.append(task.id)
                    session.update_task_status(task.id, TaskStatus.BLOCKED)
                    session.save()
                    self._emit(loop, "task_failed", {
                        "task_id": task.id,
                        "error": str(exc),
                    })

            self._emit(loop, "session_complete", {
                "done": done_ids,
                "failed": failed_ids,
            })

            # Auto-advance lifecycle EXECUTING → COMPLETE when all tasks done
            if not self._cancel.is_set():
                remaining_todo = sum(1 for t in session.tasks if t.status == TaskStatus.TODO)
                if remaining_todo == 0:
                    try:
                        from orchid.lifecycle import ProjectLifecycle
                        lc = ProjectLifecycle.load(Path(self.project_path))
                        if lc.current_phase() == "EXECUTING":
                            lc.advance("COMPLETE")
                            self._emit(loop, "advance_done", {"phase": "COMPLETE"})
                    except Exception as lc_exc:  # noqa: BLE001
                        logger.warning("Failed to auto-advance lifecycle to COMPLETE: %s", lc_exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Web runner session failed: %s", exc)
            self._emit(loop, "error", {"message": str(exc)})
        finally:
            with self._lock:
                self.current_task = ""


# ── Pydantic request models ───────────────────────────────────────────────────

if _FASTAPI_AVAILABLE:
    class CreateTaskBody(BaseModel):
        title: str
        type: str = "draft"
        priority: int = 2
        depends_on: list[str] = []
        model: str | None = None
        description: str = ""

    class PatchTaskBody(BaseModel):
        status: str  # done | blocked | cancelled | skipped | todo | in_progress

    class RunBody(BaseModel):
        mode: str = "auto"
        code_model: str | None = None

    class RunTaskBody(BaseModel):
        code_model: str | None = None

    class RecallBody(BaseModel):
        query: str
        n: int = 5

    class SearchBody(BaseModel):
        query: str

    # ── V2 lifecycle request models ────────────────────────────────────────────

    class DiscussionBody(BaseModel):
        message: str
        provider_override: str | None = None

    class AdvanceBody(BaseModel):
        confirm: bool = True
        provider_override: str | None = None

    class ApproveBody(BaseModel):
        auto_future: bool = False

    class CreateProjectBody(BaseModel):
        name: str
        description: str = ""
        project_type: str | None = None
        base_dir: str | None = None
        confirm_path: bool = True

    class SaveArtifactBody(BaseModel):
        content: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_persistent_config(path: str) -> dict[str, Any]:
    """Read persistent section from project's .orchid.yaml."""
    orchid_yaml = Path(path) / ".orchid.yaml"
    if not orchid_yaml.exists():
        return {}
    try:
        import yaml  # pyyaml
        data = yaml.safe_load(orchid_yaml.read_text(encoding="utf-8"))
        return (data or {}).get("persistent", {})
    except Exception:
        return {}


def _last_session_timestamp(path: str) -> str | None:
    """Return ISO timestamp of most recent session log, or None."""
    log_dir = Path(path) / ".orchid" / "session_logs"
    if not log_dir.exists():
        return None
    logs = sorted(log_dir.glob("session_*.jsonl"))
    if not logs:
        return None
    mtime = logs[-1].stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=UTC).isoformat()


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(
    project_paths: list[str],
    watch_dirs: list[str] | None = None,
    depth: int = 2,
    exclude: list[str] | None = None,
    enable_telegram: bool = False,
    enable_slack: bool = False,
) -> Any:
    """Build and return the FastAPI application.

    Args:
        project_paths: Explicit project directories (always registered).
        watch_dirs: Directories to auto-scan for orchid projects. When provided,
            ProjectDiscovery watches for .orchid.yaml created/deleted and
            dynamically registers/unregisters projects.
        depth: Max directory depth for auto-discovery scanning.
        exclude: Directory names to exclude from scanning.
    """
    global _discovery, _agent_manager, _central_bot_manager

    if not _FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI not installed. Run: uv pip install 'fastapi>=0.110.0' 'uvicorn[standard]>=0.27.0'"
        )

    # Register explicit projects first
    _setup_projects(project_paths)

    # Set up auto-discovery if watch_dirs provided
    discovered_paths: list[str] = []
    if watch_dirs:
        from orchid.discovery import ProjectDiscovery
        _discovery = ProjectDiscovery(
            watch_dirs=[Path(d) for d in watch_dirs],
            explicit_projects=[Path(p) for p in project_paths],
            depth=depth,
            exclude=exclude,
        )
        # Scan immediately to find existing projects
        scanned = _discovery.scan()
        for proj_path in scanned:
            proj_str = str(proj_path)
            if proj_str not in project_paths:
                _register_project(proj_str)
                discovered_paths.append(proj_str)
        logger.info("Discovery scan found %d project(s)", len(scanned))

    from orchid import config as cfg_mod
    cors_origins = cfg_mod.get("web.cors_origins", [
        "http://localhost:5173",
        "https://orchid.scheidy.com",
    ])

    # Set up CentralBotManager when bots are enabled
    if enable_telegram or enable_slack:
        if _discovery is not None:
            try:
                from orchid.interfaces.central_bot import CentralBotManager
                _central_bot_manager = CentralBotManager.from_env(_discovery)
                # Disable components not requested
                if not enable_telegram:
                    _central_bot_manager._telegram_token = None
                if not enable_slack:
                    _central_bot_manager._slack_bot_token = None
                    _central_bot_manager._slack_app_token = None
                logger.info("CentralBotManager configured (telegram=%s, slack=%s)",
                            enable_telegram, enable_slack)
            except Exception as exc:
                logger.warning("Failed to configure CentralBotManager: %s", exc)
        else:
            logger.warning("--telegram/--slack requires --watch-dir for project discovery")

    @asynccontextmanager
    async def _lifespan(app: FastAPI):  # type: ignore[name-defined]
        global _main_loop
        _main_loop = asyncio.get_running_loop()

        # Start file watcher for auto-discovery
        if _discovery is not None:
            def _on_discovery_change() -> None:
                """Handles additions only — triggered by creation events.

                Never computes a removal diff, so a momentarily incomplete
                scan() cannot accidentally unregister existing projects.
                """
                loop = _main_loop
                if loop is None or loop.is_closed():
                    return
                current_paths = set(_discovery.scan())
                with _state_lock:
                    known_paths = set(_projects.values())

                for p in current_paths - known_paths:
                    pid = _register_project(str(p))
                    if pid and loop and not loop.is_closed():
                        _broadcast_to_all(
                            {"type": "project_added", "data": {"project_id": pid, "path": str(p)}},
                            loop,
                        )
                        logger.info("Auto-registered project: %s", pid)
                    if pid and _central_bot_manager:
                        try:
                            _central_bot_manager.on_project_added(str(p))
                        except Exception as _exc:
                            logger.warning("CentralBotManager.on_project_added error: %s", _exc)

            def _on_project_removed(path: Path) -> None:
                """Handles removal of one specific project path.

                Called by discovery with the exact path that was deleted,
                after discovery has confirmed it no longer exists on disk.
                Only that project is unregistered — all others are untouched.
                """
                loop = _main_loop
                if loop is None or loop.is_closed():
                    return
                # Never remove explicitly provided projects
                if str(path) in project_paths:
                    return
                pid = _unregister_project(str(path))
                if pid and loop and not loop.is_closed():
                    _broadcast_to_all(
                        {"type": "project_removed", "data": {"project_id": pid}},
                        loop,
                    )
                    logger.info("Auto-unregistered project: %s", pid)
                if pid and _central_bot_manager:
                    try:
                        _central_bot_manager.on_project_removed(str(path))
                    except Exception as _exc:
                        logger.warning("CentralBotManager.on_project_removed error: %s", _exc)

            _discovery.watch(_on_discovery_change, on_removed=_on_project_removed)

        # Start central bot manager if enabled
        if _central_bot_manager is not None:
            try:
                _central_bot_manager.start()
                logger.info("CentralBotManager started")
            except Exception as exc:
                logger.warning("CentralBotManager failed to start: %s", exc)

        # Phase 2: orphan recovery — check for projects interrupted by a previous crash
        with _state_lock:
            _all_paths = list(_projects.values())
        for _proj_path in _all_paths:
            try:
                from orchid.runner import BackgroundRunner as _BR
                _br = _BR.__new__(_BR)  # lightweight instance just for recovery scan
                _br._lock = __import__("threading").Lock()
                _br._states = {}
                _br.recover_orphans(_proj_path)
            except Exception as _rec_exc:
                logger.warning("Orphan recovery failed for %s: %s", _proj_path, _rec_exc)


        # Start cron engine for scheduled tasks (D0061)
        global _cron_engine
        try:
            from orchid.cron.engine import get_engine as _get_cron_engine
            _cron_engine = _get_cron_engine()
            _cron_engine.start()
            logger.info("CronEngine started")
        except Exception as _cron_exc:
            logger.warning("CronEngine failed to start: %s", _cron_exc)
        yield

        # Phase 1: graceful shutdown — stop all runners cleanly before exit
        _shutdown_timeout = float(__import__("orchid.config", fromlist=["get"]).get(
            "runner.shutdown_timeout", 30))
        logger.info("Graceful shutdown: waiting up to %.0fs for running tasks", _shutdown_timeout)

        # Stop _WebProjectRunner instances (web-server-managed runs)
        with _state_lock:
            _active_runners = list(_runners.values())
        for _wr in _active_runners:
            _wr.stop()

        # Wait for runner futures
        import time as _time
        _deadline = _time.monotonic() + _shutdown_timeout
        for _wr in _active_runners:
            _rem = _deadline - _time.monotonic()
            if _rem <= 0:
                break
            if _wr._future is not None and not _wr._future.done():
                try:
                    _wr._future.result(timeout=_rem)
                except Exception:
                    pass

        if _discovery is not None:
            _discovery.stop()
        if _agent_manager is not None:
            _agent_manager.stop()
        if _central_bot_manager is not None:
            try:
                _central_bot_manager.stop()
            except Exception as exc:
                logger.warning("CentralBotManager stop error: %s", exc)

        if _cron_engine is not None:
            try:
                _cron_engine.stop()
            except Exception as exc:
                logger.warning("CronEngine stop error: %s", exc)

    _init_auth_globals()

    app = FastAPI(title="Orchid", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register scheduler API routes (D0061)
    try:
        from orchid.cron.api import register_routes as _register_cron_routes
        _register_cron_routes(app)
        logger.debug("Scheduler API routes registered")
    except Exception as _cron_api_exc:
        logger.warning("Scheduler API routes not registered: %s", _cron_api_exc)

    # Register vault + notification config routes (D0062 / Phase 2)
    try:
        from orchid.vault.api import register_routes as _register_vault_routes
        _register_vault_routes(app)
        logger.debug("Vault API routes registered")
    except Exception as _vault_exc:
        logger.warning("Vault API routes not registered: %s", _vault_exc)

    # Register MCP catalog routes (Phase 3)
    try:
        from orchid.mcp.catalog_api import (
            register_admin_routes as _register_mcp_admin,
            register_user_routes as _register_mcp_user,
        )
        _register_mcp_admin(app)
        _register_mcp_user(app)
        logger.debug("MCP catalog routes registered")
    except Exception as _mcp_exc:
        logger.warning("MCP catalog routes not registered: %s", _mcp_exc)

    # ── Auth endpoints ────────────────────────────────────────────────────────

    if _AUTH_AVAILABLE:
        @app.post("/api/auth/register")
        async def auth_register(data: dict, request: Request):
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
            _log_audit(user, AuditAction.REGISTER, user.user_id, "success", request)
            return {"user_id": user.user_id, "username": user.username, "role": user.role}

        @app.post("/api/auth/login")
        async def auth_login(data: dict, request: Request, response: Response):
            username = data.get("username", "").strip()
            password = data.get("password", "").strip()
            if not username or not password:
                raise HTTPException(400, "username and password required")
            store = _get_auth_store()
            user = store.get_user(username) or store.get_user_by_username(username)
            if user is None or not user.is_active:
                _log_audit(None, AuditAction.LOGIN_FAILED, username, "failure", request,
                           detail=json.dumps({"reason": "user not found or inactive"}))
                raise HTTPException(401, "Invalid credentials")
            if not user.password_hash or not verify_password(password, user.password_hash):
                _log_audit(user, AuditAction.LOGIN_FAILED, username, "failure", request,
                           detail=json.dumps({"reason": "invalid password"}))
                raise HTTPException(401, "Invalid credentials")
            access_token = issue_access_token(user)
            refresh_raw, rt = issue_refresh_token(user)
            store.store_refresh_token(rt)
            _set_auth_cookies(response, access_token, refresh_raw)
            _log_audit(user, AuditAction.LOGIN, user.user_id, "success", request)
            return {"user_id": user.user_id, "username": user.username, "role": user.role,
                    "access_token": access_token}

        @app.post("/api/auth/refresh")
        async def auth_refresh(request: Request, response: Response):
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
            _log_audit(user, AuditAction.TOKEN_REFRESHED, user.user_id, "success", request)
            return {"user_id": user.user_id, "username": user.username, "access_token": new_access}

        @app.post("/api/auth/token")
        async def auth_token(body: dict):
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
            if current_user is None:
                return {"authenticated": False}
            return {
                "authenticated": True,
                "user_id": current_user.user_id,
                "username": current_user.username,
                "email": current_user.email,
                "role": current_user.role,
            }

        @app.put("/api/auth/me/password")
        async def auth_change_password(
            request: Request,
            current_user: User = Depends(get_current_user),
        ):
            """Allow an authenticated user to change their own password."""
            body = await request.json()
            current_pw = body.get("current_password", "")
            new_pw     = body.get("new_password", "")
            if not current_pw or not new_pw:
                raise HTTPException(400, "current_password and new_password required")
            if len(new_pw) < 8:
                raise HTTPException(400, "new_password must be at least 8 characters")
            store = _get_auth_store()
            user = store.get_user(current_user.user_id)
            if not user or not user.password_hash:
                raise HTTPException(400, "Password authentication not set up for this account")
            if not verify_password(current_pw, user.password_hash):
                _log_audit(current_user, AuditAction.LOGIN, current_user.user_id, "failure", request)
                raise HTTPException(401, "Current password is incorrect")
            user.password_hash = hash_password(new_pw)
            store.update_user(user)
            _log_audit(current_user, AuditAction.USER_UPDATED,
                       current_user.user_id, "success", request, detail='{"field":"password"}')
            return {"ok": True}

        @app.post("/api/auth/logout")
        async def auth_logout(request: Request, response: Response,
                              current_user: User | None = Depends(get_optional_user)):
            raw = request.cookies.get(_COOKIE_REFRESH)
            if raw:
                store = _get_auth_store()
                try:
                    rt = verify_refresh_token(raw, store)
                    store.revoke_refresh_token(rt.token_id)
                except AuthError:
                    pass
            _log_audit(current_user, AuditAction.LOGOUT,
                       current_user.user_id if current_user else "anonymous", "success", request)
            response.delete_cookie(_COOKIE_ACCESS)
            response.delete_cookie(_COOKIE_REFRESH)
            return {"ok": True}

        @app.get("/api/auth/users")
        async def auth_list_users(current_user: User = Depends(get_current_user)):
            if current_user.role != "admin":
                raise HTTPException(403, "Admin role required")
            store = _get_auth_store()
            users = store.list_users()
            return {"users": [
                {
                    "user_id": u.user_id,
                    "username": u.username,
                    "email": u.email,
                    "role": u.role,
                    "is_active": u.is_active,
                    "projects": u.projects,
                    "budget_usd": u.budget_usd,
                    "budget_used_usd": u.budget_used_usd,
                    "cpu_budget_seconds": u.cpu_budget_seconds,
                    "cpu_used_seconds": u.cpu_used_seconds,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in users
            ]}

        @app.put("/api/auth/users/{target_user_id}")
        async def admin_update_user(target_user_id: str, data: dict, request: Request,
                                    current_user: User = Depends(require_auth(role="admin"))):
            store = _get_auth_store()
            user = store.get_user(target_user_id)
            if user is None:
                raise HTTPException(404, "User not found")
            changes: dict = {}
            if "role" in data:
                if data["role"] not in ("user", "admin", "readonly"):
                    raise HTTPException(400, "role must be user, admin, or readonly")
                user.role = data["role"]
                changes["role"] = user.role
            if "projects" in data:
                if not isinstance(data["projects"], list):
                    raise HTTPException(400, "projects must be a list")
                user.projects = data["projects"]
                changes["projects"] = user.projects
            if "is_active" in data:
                user.is_active = bool(data["is_active"])
                changes["is_active"] = user.is_active
            if "email" in data:
                user.email = data["email"] or None
                changes["email"] = user.email
            if "budget_usd" in data:
                user.budget_usd = float(data["budget_usd"])
                changes["budget_usd"] = user.budget_usd
            if "cpu_budget_seconds" in data:
                user.cpu_budget_seconds = float(data["cpu_budget_seconds"])
                changes["cpu_budget_seconds"] = user.cpu_budget_seconds
            store.update_user(user)
            _log_audit(current_user, AuditAction.USER_UPDATED, target_user_id, "success", request,
                       detail=json.dumps(changes))
            return {"user_id": user.user_id, "username": user.username, "role": user.role,
                    "projects": user.projects, "is_active": user.is_active, "email": user.email}

        @app.delete("/api/auth/users/{target_user_id}")
        async def admin_deactivate_user(target_user_id: str, request: Request,
                                        current_user: User = Depends(require_auth(role="admin"))):
            if target_user_id == current_user.user_id:
                raise HTTPException(400, "Cannot deactivate your own account")
            store = _get_auth_store()
            user = store.get_user(target_user_id)
            if user is None:
                raise HTTPException(404, "User not found")
            user.is_active = False
            store.update_user(user)
            store.revoke_all_refresh_tokens(target_user_id)
            _log_audit(current_user, AuditAction.USER_DEACTIVATED, target_user_id, "success", request)
            return {"ok": True, "user_id": target_user_id}

        @app.post("/api/admin/users/{target_user_id}/budget/reset")
        async def admin_reset_budget(target_user_id: str, request: Request,
                                     current_user: User = Depends(require_auth(role="admin"))):
            """Reset budget_used_usd to 0.0 for a user."""
            store = _get_auth_store()
            user = store.get_user(target_user_id)
            if user is None:
                raise HTTPException(404, "User not found")
            prev = user.budget_used_usd
            user.budget_used_usd = 0.0
            store.update_user(user)
            _log_audit(current_user, AuditAction.BUDGET_RESET, target_user_id, "success",
                       request, detail=json.dumps({"prev_used_usd": prev}))
            return {"user_id": user.user_id, "budget_used_usd": 0.0}

        # ── Admin: task run monitor ───────────────────────────────────────────

        @app.get("/api/admin/runs")
        async def admin_list_runs(limit: int = 50, offset: int = 0,
                                  owner_id: str = "", status: str = "",
                                  current_user: User = Depends(require_auth(role="admin"))):
            """Return paginated task runs across all users."""
            from orchid.cron.store import TaskRunStore
            limit = min(limit, 200)
            store = TaskRunStore()
            runs = store.get_runs(owner_id=owner_id, limit=1000)  # fetch more, filter + paginate
            if status:
                runs = [r for r in runs if r.status == status]
            total = len(runs)
            page = runs[offset: offset + limit]
            return {
                "runs": [dataclasses.asdict(r) for r in page],
                "total": total, "limit": limit, "offset": offset,
            }

        # ── Admin: system config ──────────────────────────────────────────────

        @app.get("/api/admin/config")
        async def admin_get_config(current_user: User = Depends(require_auth(role="admin"))):
            """Return editable multi-user config values."""
            from orchid import config as _cfg
            return {
                "multi_user": _cfg.get("multi_user", {}),
                "web": {
                    "allow_user_mcp":      _cfg.get("web.allow_user_mcp",      True),
                    "allow_user_projects": _cfg.get("web.allow_user_projects",  True),
                    "user_portal":         _cfg.get("web.user_portal",          True),
                    "admin_console":       _cfg.get("web.admin_console",        True),
                },
            }

        @app.put("/api/admin/config")
        async def admin_update_config(data: dict, request: Request,
                                      current_user: User = Depends(require_auth(role="admin"))):
            """Persist admin-editable config keys to ~/.config/orchid/config.yaml."""
            import yaml as _yaml
            _user_cfg_path = Path.home() / ".config" / "orchid" / "config.yaml"
            try:
                existing: dict = {}
                if _user_cfg_path.exists():
                    with open(_user_cfg_path, "r", encoding="utf-8") as fh:
                        existing = _yaml.safe_load(fh) or {}

                # Only allow writing multi_user.* and web.allow_user_* keys
                _ALLOWED_MU_KEYS = {
                    "default_budget_usd", "default_cpu_seconds",
                    "allow_user_mcp", "allow_user_projects", "credential_encryption",
                }
                _ALLOWED_WEB_KEYS = {"allow_user_mcp", "allow_user_projects"}

                updated: dict = {}
                for full_key, value in data.items():
                    section, _, key = full_key.partition(".")
                    if section == "multi_user" and key in _ALLOWED_MU_KEYS:
                        existing.setdefault("multi_user", {})[key] = value
                        updated[full_key] = value
                    elif section == "web" and key in _ALLOWED_WEB_KEYS:
                        existing.setdefault("web", {})[key] = value
                        updated[full_key] = value

                _user_cfg_path.parent.mkdir(parents=True, exist_ok=True)
                with open(_user_cfg_path, "w", encoding="utf-8") as fh:
                    _yaml.dump(existing, fh, default_flow_style=False)

                # Invalidate in-memory config so next get() reflects changes
                from orchid import config as _cfg
                _cfg._config = None

                _log_audit(current_user, AuditAction.USER_UPDATED, "system_config", "success",
                           request, detail=json.dumps(updated))
                return {"ok": True, "updated": updated}
            except Exception as exc:
                raise HTTPException(500, f"Config write failed: {exc}") from exc

        @app.get("/api/audit")
        async def get_audit_log(limit: int = 50, offset: int = 0, user_id: str = "",
                                action: str = "",
                                current_user: User = Depends(require_auth(role="admin"))):
            limit = min(limit, 500)
            events, total = _get_audit_store().read(limit=limit, offset=offset,
                                                    user_id=user_id, action=action)
            return {"events": [dataclasses.asdict(e) for e in events],
                    "total": total, "limit": limit, "offset": offset}

        @app.post("/api/auth/apikeys")
        async def create_api_key(data: dict, request: Request,
                                 current_user: User = Depends(get_current_user)):
            name = data.get("name", "").strip()
            scopes = data.get("scopes", [])
            expires_days = data.get("expires_days")
            if not name:
                raise HTTPException(400, "name required")
            if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
                raise HTTPException(400, "scopes must be a list of strings")
            expires_at: datetime | None = None
            if expires_days is not None:
                try:
                    expires_at = datetime.now(UTC) + timedelta(days=int(expires_days))
                except (TypeError, ValueError):
                    raise HTTPException(400, "expires_days must be an integer")
            raw, key = issue_api_key(current_user, name, scopes, expires_at)
            _get_auth_store().store_api_key(key)
            _log_audit(current_user, AuditAction.API_KEY_CREATED, key.key_id, "success", request,
                       detail=json.dumps({"name": name, "scopes": scopes}))
            return {"key_id": key.key_id, "name": key.name, "scopes": key.scopes,
                    "secret": raw, "created_at": key.created_at, "expires_at": key.expires_at}

        @app.get("/api/auth/apikeys")
        async def list_api_keys(current_user: User = Depends(get_current_user)):
            keys = _get_auth_store().list_api_keys(current_user.user_id)
            return {"api_keys": [{"key_id": k.key_id, "name": k.name, "scopes": k.scopes,
                                   "created_at": k.created_at, "last_used": k.last_used,
                                   "expires_at": k.expires_at, "is_active": k.is_active}
                                  for k in keys]}

        @app.delete("/api/auth/apikeys/{key_id}")
        async def revoke_api_key(key_id: str, request: Request,
                                 current_user: User = Depends(get_current_user)):
            store = _get_auth_store()
            key = store.get_api_key(key_id)
            if key is None:
                raise HTTPException(404, "API key not found")
            if key.user_id != current_user.user_id and current_user.role != "admin":
                raise HTTPException(403, "Cannot revoke another user's API key")
            store.revoke_api_key(key_id)
            _log_audit(current_user, AuditAction.API_KEY_REVOKED, key_id, "success", request)
            return {"ok": True}

        @app.get("/api/auth/oauth/providers")
        async def list_oauth_providers():
            return {"providers": _provider_registry.slugs()}

        @app.get("/api/auth/oauth/{provider_slug}/start")
        async def oauth_start(provider_slug: str, code_challenge: str = "",
                              code_challenge_method: str = "S256"):
            provider = _provider_registry.get(provider_slug)
            if provider is None:
                raise HTTPException(404, f"OAuth provider '{provider_slug}' not configured")
            if code_challenge and code_challenge_method != "S256":
                raise HTTPException(400, "Only code_challenge_method=S256 is supported")
            state = _create_oauth_state(provider_slug, code_challenge, code_challenge_method)
            url = await provider.authorization_url(state, code_challenge, code_challenge_method)
            return RedirectResponse(url, status_code=302)

        async def _resolve_oauth_callback(provider_slug: str, code: str, state: str,
                                          code_verifier: str = "") -> tuple[str, str]:
            try:
                state_data = _consume_oauth_state(state)
            except AuthError as exc:
                raise HTTPException(400, str(exc))
            if state_data["provider"] != provider_slug:
                raise HTTPException(400, "OAuth state provider mismatch")
            stored_challenge = state_data.get("code_challenge", "")
            if stored_challenge:
                if not code_verifier:
                    raise HTTPException(400, "code_verifier required for PKCE flow")
                if not _verify_pkce_s256(code_verifier, stored_challenge):
                    raise HTTPException(400, "PKCE verification failed: invalid code_verifier")
            provider = _provider_registry.get(provider_slug)
            if provider is None:
                raise HTTPException(404, f"OAuth provider '{provider_slug}' not configured")
            store = _get_auth_store()
            try:
                user, _oa = await provider.handle_callback(code, store, code_verifier=code_verifier)
            except AuthError as exc:
                raise HTTPException(401, str(exc))
            except Exception as exc:
                raise HTTPException(502, f"OAuth provider error: {exc}")
            access_token = issue_access_token(user)
            refresh_raw, rt = issue_refresh_token(user)
            store.store_refresh_token(rt)
            _log_audit(user, AuditAction.OAUTH_LOGIN, provider_slug, "success",
                       detail=json.dumps({"provider": provider_slug}))
            return access_token, refresh_raw

        @app.get("/api/auth/oauth/{provider_slug}/callback")
        async def oauth_callback_get(provider_slug: str, request: Request):
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

        @app.post("/api/auth/oauth/{provider_slug}/token")
        async def oauth_token_mobile(provider_slug: str, data: dict):
            code = data.get("code", "").strip()
            state = data.get("state", "").strip()
            code_verifier = data.get("code_verifier", "").strip()
            if not code or not state:
                raise HTTPException(400, "code and state required")
            if not code_verifier:
                raise HTTPException(400, "code_verifier required for mobile PKCE flow")
            access_token, refresh_raw = await _resolve_oauth_callback(
                provider_slug, code, state, code_verifier=code_verifier)
            return {"access_token": access_token, "refresh_token": refresh_raw,
                    "token_type": "Bearer", "expires_in": 900}

    # ── Admin invite flow (D0062 / Phase 2) ──────────────────────────────────

    if _AUTH_AVAILABLE:
        @app.post("/api/admin/invite")
        async def admin_invite(data: dict, request: Request,
                               current_user=Depends(require_auth(role="admin"))):
            """Admin invites a new user by email.

            Creates an inactive User and a one-time InviteToken (48h TTL).
            Sends an email with the accept link if SMTP is configured; always
            returns ``invite_url`` so the admin can share it manually.
            """
            import uuid as _uuid
            from datetime import UTC, timedelta
            from orchid.auth.types import AuthError as _AE

            email = (data.get("email") or "").strip().lower()
            role = data.get("role", "user")
            if not email:
                raise HTTPException(400, "email required")
            if role not in ("user", "admin", "readonly"):
                raise HTTPException(400, "role must be user, admin, or readonly")

            store = _get_auth_store()
            if store.get_user_by_email(email):
                raise HTTPException(409, "A user with that email already exists")

            # Derive a default username from the email local part
            username_base = email.split("@")[0]
            # Ensure uniqueness
            username = username_base
            suffix = 0
            while store.get_user_by_username(username):
                suffix += 1
                username = f"{username_base}{suffix}"

            user_id = _uuid.uuid4().hex
            new_user = User(
                user_id=user_id,
                username=username,
                email=email,
                role=role,
                is_active=False,  # activated on invite accept
                password_hash=None,
            )
            try:
                store.add_user(new_user)
            except _AE:
                raise HTTPException(409, "User already exists")

            # Generate invite token
            token_id = f"inv_{_uuid.uuid4().hex}"
            secret = secrets.token_urlsafe(32)
            from orchid.auth.types import InviteToken as _InviteToken
            from orchid.auth.jwt import hash_password as _hp
            invite = _InviteToken(
                token_id=token_id,
                secret_hash=_hp(secret),
                user_id=user_id,
                email=email,
                invited_by=current_user.user_id,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=48),
            )
            store.store_invite(invite)

            # Build invite URL — use the request's base URL
            base = str(request.base_url).rstrip("/")
            invite_url = f"{base}/app/?invite_id={token_id}&invite_token={secret}"

            # Send email (graceful fallback if SMTP not configured)
            from orchid.auth.mailer import send_invite as _send_invite
            email_sent = _send_invite(email, invite_url, current_user.username or current_user.user_id)

            _log_audit(current_user, AuditAction.INVITE_SENT, email, "success", request,
                       detail=f"role={role}")
            return {
                "invite_url": invite_url,
                "email_sent": email_sent,
                "token_id": token_id,
                "expires_in_hours": 48,
                "user_id": user_id,
                "username": username,
            }

        @app.get("/api/auth/invite/{token_id}")
        async def validate_invite(token_id: str):
            """Public: validate an invite token and return the invitee's email.

            Used by the portal accept-invite form to show a welcome message before
            the user sets their password.  Returns 404 if the token is unknown,
            expired, or already used.
            """
            from datetime import UTC
            store = _get_auth_store()
            invite = store.get_invite(token_id)
            if invite is None or invite.is_used:
                raise HTTPException(404, "Invalid or already-used invite link")
            if datetime.now(UTC) > invite.expires_at:
                raise HTTPException(410, "Invite link has expired")
            return {"email": invite.email, "token_id": token_id}

        @app.post("/api/auth/invite/accept")
        async def accept_invite(data: dict, request: Request, response: Response):
            """Public: accept an invite, set password, activate account, issue JWT.

            Body: {token_id, invite_token, password}
            On success: sets HttpOnly cookies and returns user info (same as /api/auth/me).
            """
            from datetime import UTC
            token_id = (data.get("token_id") or "").strip()
            raw_secret = (data.get("invite_token") or "").strip()
            password = (data.get("password") or "").strip()

            if not token_id or not raw_secret or not password:
                raise HTTPException(400, "token_id, invite_token, and password required")
            if len(password) < 8:
                raise HTTPException(400, "Password must be at least 8 characters")

            store = _get_auth_store()
            invite = store.get_invite(token_id)
            if invite is None or invite.is_used:
                raise HTTPException(404, "Invalid or already-used invite link")
            if datetime.now(UTC) > invite.expires_at:
                raise HTTPException(410, "Invite link has expired")

            # Constant-time argon2 verification
            if not verify_password(raw_secret, invite.secret_hash):
                raise HTTPException(401, "Invalid invite token")

            user = store.get_user(invite.user_id)
            if user is None:
                raise HTTPException(404, "Invited user account not found")

            # Activate user and set password
            user.is_active = True
            user.password_hash = hash_password(password)
            store.update_user(user)
            store.mark_invite_used(token_id)

            # Issue JWT session
            access_token = issue_access_token(user)
            refresh_raw, rt = issue_refresh_token(user)
            store.store_refresh_token(rt)
            _set_auth_cookies(response, access_token, refresh_raw)

            _log_audit(user, AuditAction.INVITE_ACCEPTED, user.email or user.user_id,
                       "success", request)
            return {
                "user_id": user.user_id,
                "username": user.username,
                "email": user.email,
                "role": user.role,
            }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_project(project_id: str) -> str:
        if project_id not in _projects:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
        return _projects[project_id]

    def _load_session(project_path: str):
        from orchid.session import Session
        s = Session(project_dir=project_path)
        s.load()
        return s

    def _load_task_failures(project_path: str) -> dict[str, str]:
        """Return {task_id: reason} for task_failed events in the most recent session log."""
        import json as _json
        log_dir = Path(project_path) / ".orchid" / "session_logs"
        if not log_dir.exists():
            return {}
        logs = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            return {}
        failures: dict[str, str] = {}
        try:
            for line in logs[0].read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = _json.loads(line)
                if rec.get("type") == "task_failed":
                    tid = rec.get("task_id", "")
                    reason = rec.get("reason", "failed")
                    if tid:
                        failures[tid] = reason
        except Exception:
            pass
        return failures

    def _task_dict(t: Any, last_error: str | None = None) -> dict[str, Any]:
        return {
            "id": t.id,
            "title": t.title,
            "status": t.status.value,
            "type": t.type,
            "priority": t.priority,
            "description": t.description,
            "depends_on": t.depends_on,
            "model_override": t.model_override,
            "last_error": last_error,
        }

    # ── REST endpoints ────────────────────────────────────────────────────────

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "projects": len(_projects)}

    @app.get("/api/providers")
    async def get_providers():
        from orchid.providers.registry import get_registry
        registry = get_registry()
        return registry.all_status()

    @app.get("/api/projects")
    async def get_projects():
        with _state_lock:
            items = list(_projects.items())
        result = []
        for pid, path in items:
            try:
                s = _load_session(path)
                from orchid.memory.state import TaskStatus
                todo = sum(1 for t in s.tasks if t.status == TaskStatus.TODO)
                done = sum(1 for t in s.tasks if t.status == TaskStatus.DONE)
                inprog = sum(1 for t in s.tasks if t.status == TaskStatus.IN_PROGRESS)
                blocked = sum(1 for t in s.tasks if t.status == TaskStatus.BLOCKED)
                runner = _runners.get(pid)
                persistent = _read_persistent_config(path)
                # Read active flag from .orchid.yaml (default True)
                try:
                    import yaml as _yaml
                    _oyaml = Path(path) / ".orchid.yaml"
                    _yd = _yaml.safe_load(_oyaml.read_text(encoding="utf-8")) or {} if _oyaml.exists() else {}
                    is_active = _yd.get("active", True)
                except Exception:
                    is_active = True
                result.append({
                    "id": pid,
                    "name": s.project_name,
                    "path": path,
                    "description": s.project_description,
                    "task_counts": {
                        "todo": todo,
                        "done": done,
                        "in_progress": inprog,
                        "blocked": blocked,
                    },
                    "running": runner.is_running() if runner else False,
                    "persistent": persistent,
                    "last_session": _last_session_timestamp(path),
                    "active": is_active,
                })
            except Exception as exc:
                result.append({"id": pid, "path": path, "error": str(exc)})
        return result

    @app.get("/api/discovery")
    async def get_discovery():
        """Return auto-discovery state: watch dirs, discovered projects, last scan."""
        if _discovery is None:
            return {
                "enabled": False,
                "watch_dirs": [],
                "discovered_projects": [],
                "last_scan": None,
            }
        with _state_lock:
            current_projects = list(_projects.keys())
        return {
            "enabled": True,
            "watch_dirs": [str(d) for d in _discovery.watch_dirs],
            "discovered_projects": current_projects,
            "last_scan": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/projects/{project_id}/status")
    async def get_project_status(project_id: str):
        path = _get_project(project_id)
        s = _load_session(path)
        failures = _load_task_failures(path)
        runner = _runners.get(project_id)
        return {
            "id": project_id,
            "name": s.project_name,
            "path": path,
            "description": s.project_description,
            "tasks": [_task_dict(t, failures.get(t.id)) for t in s.tasks],
            "decisions": s.decisions[-10:],
            "hot_memory": s.hot_memory,
            "running": runner.is_running() if runner else False,
            "current_task": runner.current_task if runner else "",
            "tasks_done": runner.tasks_done if runner else 0,
            "persistent": _read_persistent_config(path),
            "last_session": _last_session_timestamp(path),
        }

    @app.get("/api/projects/{project_id}/tasks")
    async def get_tasks(project_id: str):
        path = _get_project(project_id)
        s = _load_session(path)
        failures = _load_task_failures(path)
        return [_task_dict(t, failures.get(t.id)) for t in s.tasks]

    @app.post("/api/projects/{project_id}/tasks", status_code=201)
    async def create_task(project_id: str, body: CreateTaskBody):
        path = _get_project(project_id)
        from orchid.memory.state import Task, save_tasks
        s = _load_session(path)
        tid = f"T{len(s.tasks) + 1:03d}"
        t = Task(
            id=tid,
            title=body.title,
            type=body.type,
            priority=body.priority,
            description=body.description,
            depends_on=body.depends_on,
            model_override=body.model,
        )
        s.tasks.append(t)
        save_tasks(s.tasks, path)
        return _task_dict(t)

    @app.patch("/api/projects/{project_id}/tasks/{task_id}")
    async def patch_task(project_id: str, task_id: str, body: PatchTaskBody):
        path = _get_project(project_id)
        from orchid.memory.state import TaskStatus, save_tasks
        s = _load_session(path)
        status_map = {
            "done": TaskStatus.DONE,
            "blocked": TaskStatus.BLOCKED,
            "cancelled": TaskStatus.CANCELLED,
            "todo": TaskStatus.TODO,
            "in_progress": TaskStatus.IN_PROGRESS,
            "skipped": TaskStatus.SKIPPED,
        }
        new_status = status_map.get(body.status.lower())
        if new_status is None:
            raise HTTPException(status_code=400, detail=f"Unknown status: {body.status}")
        if not s.update_task_status(task_id, new_status):
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        save_tasks(s.tasks, path)
        task = next(t for t in s.tasks if t.id == task_id)
        return _task_dict(task)

    @app.get("/api/projects/{project_id}/decisions")
    async def get_decisions(project_id: str):
        path = _get_project(project_id)
        from orchid.memory.decisions import load_decisions
        return load_decisions(path)

    @app.get("/api/projects/{project_id}/sessions")
    async def get_sessions(project_id: str):
        path = _get_project(project_id)
        log_dir = Path(path) / ".orchid" / "session_logs"
        if not log_dir.exists():
            return []
        sessions = []
        for f in sorted(log_dir.glob("session_*.jsonl"), reverse=True)[:50]:
            stat = f.stat()
            sessions.append({
                "id": f.stem,
                "filename": f.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            })
        return sessions

    @app.get("/api/projects/{project_id}/sessions/{session_id}")
    async def get_session(project_id: str, session_id: str):
        path = _get_project(project_id)
        log_dir = Path(path) / ".orchid" / "session_logs"
        candidates = list(log_dir.glob(f"{session_id}*"))
        if not candidates:
            raise HTTPException(status_code=404, detail="Session not found")
        log_file = candidates[0]
        lines = []
        import json as _json
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(_json.loads(line))
            except Exception:
                lines.append({"raw": line})
        return {"id": session_id, "entries": lines}

    @app.get("/api/projects/{project_id}/metrics")
    async def get_metrics(project_id: str):
        """Return parsed task metrics from .orchid/task_metrics.jsonl (T085)."""
        import json as _json
        path = _get_project(project_id)
        metrics_path = Path(path) / ".orchid" / "task_metrics.jsonl"
        if not metrics_path.exists():
            return []
        seen: dict[str, dict] = {}
        for line in metrics_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = _json.loads(line)
                seen[rec["task_id"]] = rec  # last entry per task_id wins
            except Exception:
                pass
        return list(seen.values())

    @app.post("/api/projects/{project_id}/recall")
    async def recall(project_id: str, body: RecallBody):
        path = _get_project(project_id)
        from orchid import config as cfg_mod
        from orchid.memory.vector import VectorMemory
        cfg_mod.configure_for_project(path)
        vm = VectorMemory(project_dir=path)
        if not vm.available:
            raise HTTPException(status_code=503, detail="Vector memory not available")
        results = vm.query(body.query, n=body.n)
        return results

    @app.post("/api/projects/{project_id}/search")
    async def search(project_id: str, body: SearchBody):
        path = _get_project(project_id)
        from orchid import config as cfg_mod
        from orchid.tools.search import WebSearchTool, reset_backend_cache
        cfg_mod.configure_for_project(path)
        reset_backend_cache()
        tool = WebSearchTool(project_name=Path(path).name)
        results = tool.search(body.query, n=5)
        return results

    @app.post("/api/projects/{project_id}/run")
    async def start_run(project_id: str, body: RunBody):
        _get_project(project_id)
        runner = _runners.get(project_id)
        if runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")
        if runner.is_running():
            raise HTTPException(status_code=409, detail="A run is already in progress")
        loop = _main_loop or asyncio.get_running_loop()
        rid = runner.start(loop, mode=body.mode, code_model=body.code_model)
        if not rid:
            raise HTTPException(status_code=409, detail="Failed to start run")
        return {"run_id": rid}

    @app.delete("/api/projects/{project_id}/run")
    async def stop_run(project_id: str):
        _get_project(project_id)
        runner = _runners.get(project_id)
        if runner is None or not runner.is_running():
            raise HTTPException(status_code=409, detail="No run in progress")
        runner.stop()
        return {"stopped": True}

    @app.get("/api/projects/{project_id}/run/status")
    async def run_status(project_id: str):
        _get_project(project_id)
        runner = _runners.get(project_id)
        if runner is None:
            return {"running": False, "current_task": "", "tasks_done": 0, "run_id": ""}
        current = runner.current_task
        task_id = current.split(":")[0].strip() if current else ""
        return {
            "running": runner.is_running(),
            "current_task": current,
            "tasks_done": runner.tasks_done,
            "run_id": runner.run_id,
            "suspended": runner.is_suspended(task_id) if task_id else False,
        }

    @app.post("/api/projects/{project_id}/tasks/{task_id}/suspend")
    async def suspend_task(project_id: str, task_id: str):
        _get_project(project_id)
        runner = _runners.get(project_id)
        if runner is None or not runner.suspend_task(task_id):
            raise HTTPException(status_code=404, detail=f"Task {task_id} is not running")
        return {"ok": True, "task_id": task_id, "state": "suspended"}

    @app.post("/api/projects/{project_id}/tasks/{task_id}/resume")
    async def resume_task(project_id: str, task_id: str):
        _get_project(project_id)
        runner = _runners.get(project_id)
        if runner is None or not runner.resume_task(task_id):
            raise HTTPException(status_code=404, detail=f"Task {task_id} is not running")
        return {"ok": True, "task_id": task_id, "state": "running"}

    @app.post("/api/projects/{project_id}/tasks/{task_id}/run")
    async def run_single_task(project_id: str, task_id: str, body: RunTaskBody):
        _get_project(project_id)
        runner = _runners.get(project_id)
        if runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")
        if runner.is_running():
            raise HTTPException(status_code=409, detail="A run is already in progress")
        loop = _main_loop or asyncio.get_running_loop()
        rid = runner.start_single_task(loop, task_id, code_model=body.code_model)
        if not rid:
            raise HTTPException(status_code=409, detail="Failed to start run")
        return {"run_id": rid, "task_id": task_id}

    @app.get("/api/projects/{project_id}/settings")
    async def get_project_settings(project_id: str):
        """Return project .orchid.yaml and .env contents with sensitive values redacted."""
        import re as _re
        path = _get_project(project_id)
        result: dict[str, Any] = {}

        orchid_yaml = Path(path) / ".orchid.yaml"
        if orchid_yaml.exists():
            result["orchid_yaml"] = orchid_yaml.read_text(encoding="utf-8")
        else:
            result["orchid_yaml"] = None

        env_file = Path(path) / ".env"
        if env_file.exists():
            raw = env_file.read_text(encoding="utf-8")
            redacted_lines = []
            for line in raw.splitlines():
                if _re.match(r"^\s*[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD|PASS)[A-Z_]*\s*=", line, _re.IGNORECASE):
                    key = line.split("=", 1)[0]
                    redacted_lines.append(f"{key}=<redacted>")
                else:
                    redacted_lines.append(line)
            result["env"] = "\n".join(redacted_lines)
        else:
            result["env"] = None

        return result

    @app.patch("/api/projects/{project_id}/active")
    async def set_project_active(project_id: str, body: dict[str, bool]):
        """Set project active/inactive status in .orchid.yaml."""
        import yaml  # pyyaml
        path = _get_project(project_id)
        orchid_yaml = Path(path) / ".orchid.yaml"
        try:
            data = yaml.safe_load(orchid_yaml.read_text(encoding="utf-8")) or {} if orchid_yaml.exists() else {}
        except Exception:
            data = {}
        data["active"] = body.get("active", True)
        orchid_yaml.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
        return {"active": data["active"]}

    @app.post("/api/projects/{project_id}/inject")
    async def inject_context(project_id: str, body: dict[str, str]):
        _get_project(project_id)
        runner = _runners.get(project_id)
        if runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")
        text = body.get("text", "")
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        runner.inject(text)
        return {"injected": True}

    @app.get("/api/version")
    async def get_version():
        try:
            from importlib.metadata import version
            v = version("orchid")
        except Exception:
            from orchid import __version__ as v
        return {"version": v}

    # ── V2 lifecycle endpoints ────────────────────────────────────────────────

    @app.get("/api/machine-profile")
    async def get_machine_profile():
        from orchid.machine_profile import MachineProfile
        p = MachineProfile.load()
        return {
            "developer_name": p.developer_name,
            "project_roots": p.project_roots,
            "preferred_stacks": p.preferred_stacks,
            "infrastructure": {
                k: v for k, v in p.infrastructure.items()
                if k not in ("local_llm", "embedding")
            },
            "defaults": p.defaults,
        }

    @app.put("/api/machine-profile")
    async def update_machine_profile(body: dict):
        from orchid.machine_profile import MachineProfile
        p = MachineProfile.load()
        if "developer_name" in body:
            p.developer_name = body["developer_name"]
        if "project_roots" in body:
            p.project_roots.update(body["project_roots"])
        if "infrastructure" in body:
            p.infrastructure.update(body["infrastructure"])
        if "defaults" in body:
            p.defaults.update(body["defaults"])
        p.save()
        return {"saved": True}

    @app.post("/api/projects", status_code=201)
    async def create_project(body: CreateProjectBody):
        from orchid.machine_profile import MachineProfile
        from orchid.project_creator import ProjectCreator
        profile = MachineProfile.load()
        creator = ProjectCreator(machine_profile=profile)
        base_dir = Path(body.base_dir).expanduser() if body.base_dir else None
        suggested = (
            (base_dir / body.name).resolve() if base_dir
            else creator.confirm_path(body.name, body.project_type).resolve()
        )
        if not body.confirm_path:
            return {"suggested_path": str(suggested)}

        loop = asyncio.get_running_loop()
        try:
            project_dir = await loop.run_in_executor(None, lambda: creator.create(
                name=body.name,
                description=body.description,
                project_type=body.project_type,
                base_dir=base_dir,
            ))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        pid = _register_project(str(project_dir))
        if pid is None:
            with _state_lock:
                for epid, epath in _projects.items():
                    if epath == str(project_dir):
                        pid = epid
                        break
        if not pid:
            raise HTTPException(status_code=500, detail="Created but failed to register project")
        loop_ref = _main_loop
        if loop_ref and not loop_ref.is_closed():
            _broadcast_to_all(
                {"type": "project_added", "data": {"project_id": pid, "path": str(project_dir)}},
                loop_ref,
            )
        return {"project_id": pid, "path": str(project_dir)}

    @app.get("/api/projects/{project_id}/lifecycle")
    async def get_lifecycle(project_id: str):
        path = _get_project(project_id)
        from orchid.lifecycle import ProjectLifecycle
        lc = ProjectLifecycle.load(Path(path))
        artifact_names = ["REQUIREMENTS.md", "ARCHITECTURE.md", "MILESTONES.md", "tasks.md"]
        return {
            "phase": lc.state.phase,
            "project_name": lc.state.project_name,
            "artifacts": {n: (Path(path) / n).exists() for n in artifact_names},
            "gates": lc.state.gates,
            "discussion_turns": lc.state.discussion_turns,
            "slack_channel": lc.state.slack_channel,
            "created_at": lc.state.created_at,
            "last_activity": lc.state.last_activity,
            "current_milestone": lc.state.current_milestone,
        }

    @app.post("/api/projects/{project_id}/lifecycle/validate-executing")
    async def validate_executing(project_id: str):
        """Check if all tasks are complete while in EXECUTING phase; advance to COMPLETE if so."""
        path = _get_project(project_id)
        from orchid.lifecycle import ProjectLifecycle
        from orchid.memory.state import TaskStatus
        from orchid.session import Session
        proj_path = Path(path)
        lc = ProjectLifecycle.load(proj_path)
        if lc.current_phase() != "EXECUTING":
            return {"phase": lc.current_phase(), "advanced": False, "reason": "not in EXECUTING phase"}
        session = Session(project_dir=path)
        session.load()
        remaining = sum(1 for t in session.tasks if t.status == TaskStatus.TODO)
        if remaining > 0:
            return {"phase": "EXECUTING", "advanced": False, "remaining": remaining,
                    "reason": f"{remaining} task(s) still pending"}
        lc.advance("COMPLETE")
        return {"phase": "COMPLETE", "advanced": True, "remaining": 0}

    @app.get("/api/projects/{project_id}/discussion")
    async def get_discussion(project_id: str):
        path = _get_project(project_id)
        from orchid.discussion import DiscussionHistory
        history = DiscussionHistory.load(Path(path))
        return {
            "turns": history.get_full_history()[-50:],
            "context_md": history.get_context_md(),
            "turn_count": history.turn_count(),
        }

    @app.post("/api/projects/{project_id}/discussion")
    async def post_discussion(project_id: str, body: DiscussionBody):
        path = _get_project(project_id)
        loop = asyncio.get_running_loop()

        def _run():
            from orchid.agents.discussion_agent import DiscussionAgent
            from orchid.discussion import DiscussionHistory
            from orchid.lifecycle import ProjectLifecycle
            proj_path = Path(path)
            history = DiscussionHistory.load(proj_path)
            lc = ProjectLifecycle.load(proj_path)
            if lc.current_phase() == "NEW":
                lc.advance("DISCUSSING")
            history.append("user", body.message)
            lc.state.discussion_turns += 1
            lc.save()
            agent = DiscussionAgent(proj_path, cli_override=body.provider_override)
            response = agent.run(body.message, history)
            history.append("agent", response.message)
            if response.context_updates:
                agent.update_context(history, response.context_updates)
            return {
                "response": response.message,
                "ready_to_advance": response.ready_to_advance,
                "suggestions": response.suggestions,
                "phase": lc.current_phase(),
            }

        try:
            return await loop.run_in_executor(None, _run)
        except Exception as exc:
            logger.exception("Discussion agent error")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/projects/{project_id}/discussion")
    async def reset_discussion(project_id: str):
        """Reset discussion history and lifecycle phase back to NEW."""
        path = _get_project(project_id)
        proj_path = Path(path)
        discussion_dir = proj_path / ".orchid" / "discussion"
        conv_path = discussion_dir / "conversation.jsonl"
        context_path = discussion_dir / "context.md"
        if conv_path.exists():
            conv_path.unlink()
        if context_path.exists():
            context_path.unlink()
        from orchid.lifecycle import ProjectLifecycle
        lc = ProjectLifecycle.load(proj_path)
        lc.state.phase = "NEW"
        lc.state.discussion_turns = 0
        lc.save()
        logger.info("Discussion reset for project %s", project_id)
        return {"status": "reset", "phase": "NEW"}

    @app.post("/api/projects/{project_id}/advance")
    async def advance_project(project_id: str, body: AdvanceBody):
        path = _get_project(project_id)
        if not body.confirm:
            from orchid.lifecycle import ProjectLifecycle
            lc = ProjectLifecycle.load(Path(path))
            return {"phase": lc.current_phase()}

        loop = asyncio.get_running_loop()
        manager = _managers.get(project_id)

        def _emit(ev_type: str, data: Any) -> None:
            if manager and loop and not loop.is_closed():
                manager.broadcast_sync({"type": ev_type, "data": data}, loop)

        def _run():
            from orchid.agents.product_manager import ProductManagerAgent
            from orchid.agents.project_manager import ProjectManagerAgent
            from orchid.lifecycle import ProjectLifecycle
            proj_path = Path(path)
            lc = ProjectLifecycle.load(proj_path)
            phase = lc.current_phase()
            po = body.provider_override

            if phase in ("NEW", "DISCUSSING"):
                if phase == "NEW":
                    lc.advance("DISCUSSING")
                _emit("advance_status", {"status": "Generating REQUIREMENTS.md…"})
                pm = ProductManagerAgent(proj_path, cli_override=po)
                r = pm.run()
                _emit("advance_artifact", {"name": "requirements", "path": str(r.requirements_path)})
                _emit("advance_status", {"status": "Generating ARCHITECTURE.md…"})
                _emit("advance_artifact", {"name": "architecture", "path": str(r.architecture_path)})
                lc.advance("REQUIREMENTS")
                _emit("advance_status", {"status": "Generating MILESTONES.md and tasks.md…"})
                pmgr = ProjectManagerAgent(proj_path, cli_override=po)
                r2 = pmgr.run()
                _emit("advance_artifact", {"name": "milestones", "path": str(r2.milestones_path)})
                _emit("advance_artifact", {"name": "tasks", "path": str(r2.tasks_path)})
                lc.advance("PLANNING")
                lc.advance("READY")

            elif phase == "REQUIREMENTS":
                _emit("advance_status", {"status": "Generating MILESTONES.md and tasks.md…"})
                pmgr = ProjectManagerAgent(proj_path, cli_override=po)
                r = pmgr.run()
                _emit("advance_artifact", {"name": "milestones", "path": str(r.milestones_path)})
                _emit("advance_artifact", {"name": "tasks", "path": str(r.tasks_path)})
                lc.advance("PLANNING")
                lc.advance("READY")

            elif phase == "PLANNING":
                lc.advance("READY")

            new_phase = lc.current_phase()
            _emit("advance_done", {"phase": new_phase})
            return {"phase": new_phase}

        try:
            return await loop.run_in_executor(None, _run)
        except Exception as exc:
            logger.exception("Advance error")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/projects/{project_id}/approve")
    async def approve_gate(project_id: str, body: ApproveBody):
        path = _get_project(project_id)
        from orchid.gates import GateStatus, GateSystem
        from orchid.lifecycle import ProjectLifecycle
        proj_path = Path(path)
        lc = ProjectLifecycle.load(proj_path)
        gates = GateSystem(lc)
        current = lc.current_phase()
        next_phases = [p for p in lc.valid_next_phases() if p != "DISCUSSING"]
        if not next_phases:
            raise HTTPException(status_code=400, detail=f"No valid transitions from {current}")
        to_phase = next_phases[0]
        status = gates.check_gate(to_phase)
        if status == GateStatus.BLOCKED:
            raise HTTPException(status_code=409, detail=f"Prerequisites not met for {to_phase}")
        gates.approve(to_phase)
        if body.auto_future:
            for p in lc.valid_next_phases():
                if p != "DISCUSSING":
                    key = lc._transition_key(to_phase, p)
                    lc.state.gates.setdefault(key, {})["type"] = "auto"
            lc.save()
        lc.advance(to_phase)
        new_nexts = [p for p in lc.valid_next_phases() if p != "DISCUSSING"]
        return {"phase": lc.current_phase(), "next_gate": new_nexts[0] if new_nexts else None}

    @app.get("/api/projects/{project_id}/artifacts")
    async def get_artifacts(project_id: str):
        path = _get_project(project_id)
        proj_path = Path(path)
        artifact_map = {
            "requirements": "REQUIREMENTS.md",
            "architecture": "ARCHITECTURE.md",
            "milestones": "MILESTONES.md",
            "tasks": "tasks.md",
        }
        result = {}
        for key, filename in artifact_map.items():
            fp = proj_path / filename
            exists = fp.exists()
            content = None
            if exists:
                try:
                    raw = fp.read_text(encoding="utf-8")
                    content = raw[:50000] + "\n…(truncated)" if len(raw) > 50000 else raw
                except Exception:
                    pass
            result[key] = {"exists": exists, "content": content, "path": str(fp)}
        return result

    @app.patch("/api/projects/{project_id}/artifacts/{artifact_name}")
    async def save_artifact(project_id: str, artifact_name: str, body: SaveArtifactBody):
        path = _get_project(project_id)
        name_map = {
            "requirements": "REQUIREMENTS.md",
            "architecture": "ARCHITECTURE.md",
            "milestones": "MILESTONES.md",
            "tasks": "tasks.md",
        }
        filename = name_map.get(artifact_name)
        if not filename:
            raise HTTPException(status_code=400, detail=f"Unknown artifact: {artifact_name}")
        fp = Path(path) / filename
        fp.write_text(body.content, encoding="utf-8")
        return {"saved": True, "path": str(fp)}

    # ── WebSocket ─────────────────────────────────────────────────────────────

    @app.websocket("/ws/{project_id}")
    async def ws_endpoint(ws: WebSocket, project_id: str):
        if project_id not in _managers:
            await ws.close(code=4004, reason="Project not found")
            return
        manager = _managers[project_id]
        await manager.connect(ws)
        runner = _runners.get(project_id)
        await ws.send_json({"type": "connected", "data": {
            "project_id": project_id,
            "running": runner.is_running() if runner else False,
            "current_task": runner.current_task if runner else "",
        }})
        from orchid import config as _cfg
        _heartbeat_s = float(_cfg.get("web.ws_heartbeat_s", 30.0))
        _send_timeout = float(_cfg.get("web.ws_send_timeout", 5.0))
        try:
            while True:
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=_heartbeat_s)
                except TimeoutError:
                    # Heartbeat ping — closes dead connections quickly
                    try:
                        await asyncio.wait_for(
                            ws.send_json({"type": "ping"}), timeout=_send_timeout
                        )
                    except Exception:
                        break
        except WebSocketDisconnect:
            pass
        finally:
            manager.remove(ws)

    @app.websocket("/ws/{project_id}/discussion")
    async def ws_discussion(ws: WebSocket, project_id: str):
        """Bidirectional WebSocket for discussion agent.

        Client sends: {"message": "...", "provider_override": null}
        Server sends:
          {"type": "thinking"}
          {"type": "token", "data": "...full response..."}
          {"type": "done", "data": {ready_to_advance, suggestions}}
          {"type": "error", "data": "..."}
        """
        if project_id not in _projects:
            await ws.close(code=4004, reason="Project not found")
            return
        path = _projects[project_id]
        await ws.accept()
        loop = asyncio.get_running_loop()
        try:
            while True:
                data = await ws.receive_json()
                message = data.get("message", "").strip()
                provider_override = data.get("provider_override")
                if not message:
                    continue
                await ws.send_json({"type": "thinking"})

                def _process(msg=message, po=provider_override):
                    from orchid.agents.discussion_agent import DiscussionAgent
                    from orchid.discussion import DiscussionHistory
                    from orchid.lifecycle import ProjectLifecycle
                    proj_path = Path(path)
                    history = DiscussionHistory.load(proj_path)
                    lc = ProjectLifecycle.load(proj_path)
                    if lc.current_phase() == "NEW":
                        lc.advance("DISCUSSING")
                    history.append("user", msg)
                    lc.state.discussion_turns += 1
                    lc.save()
                    agent = DiscussionAgent(proj_path, cli_override=po)
                    response = agent.run(msg, history)
                    history.append("agent", response.message)
                    if response.context_updates:
                        agent.update_context(history, response.context_updates)
                    return response

                try:
                    response = await loop.run_in_executor(None, _process)
                    await ws.send_json({"type": "token", "data": response.message})
                    await ws.send_json({"type": "done", "data": {
                        "ready_to_advance": response.ready_to_advance,
                        "suggestions": response.suggestions,
                    }})
                except Exception as exc:
                    logger.exception("Discussion WS agent error")
                    await ws.send_json({"type": "error", "data": str(exc)})
        except WebSocketDisconnect:
            pass

    # ── Frontend static files ─────────────────────────────────────────────────
    #
    # Two SPAs:
    #   /          → main admin/power-user app  (web_ui/dist)
    #   /app/*     → user portal                (portal/dist)
    #
    # Role-based redirect at /:
    #   - authed non-admin  → 302 /app/
    #   - authed admin      → serve main SPA (they see everything)
    #   - not authed        → serve main SPA (login page handles it)

    # ── Portal SPA (/app/*) ────────────────────────────────────────────────
    if _PORTAL_DIST_DIR.exists():
        portal_assets = _PORTAL_DIST_DIR / "assets"
        if portal_assets.exists():
            app.mount("/app/assets", StaticFiles(directory=portal_assets), name="portal_assets")

        def _portal_index_response() -> FileResponse:
            r = FileResponse(_PORTAL_DIST_DIR / "index.html", media_type="text/html")
            r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            r.headers["Pragma"] = "no-cache"
            r.headers["Expires"] = "0"
            return r

        @app.get("/app", include_in_schema=False)
        @app.get("/app/", include_in_schema=False)
        async def serve_portal_index():
            return _portal_index_response()

        @app.get("/app/{full_path:path}", include_in_schema=False)
        async def serve_portal_spa(full_path: str):
            file_path = _PORTAL_DIST_DIR / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return _portal_index_response()

    # ── Admin Console SPA (/admin/*) ──────────────────────────────────────
    if _ADMIN_DIST_DIR.exists():
        admin_assets = _ADMIN_DIST_DIR / "assets"
        if admin_assets.exists():
            app.mount("/admin/assets", StaticFiles(directory=admin_assets), name="admin_assets")

        def _admin_index_response() -> FileResponse:
            r = FileResponse(_ADMIN_DIST_DIR / "index.html", media_type="text/html")
            r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            r.headers["Pragma"] = "no-cache"
            r.headers["Expires"] = "0"
            return r

        @app.get("/admin", include_in_schema=False)
        @app.get("/admin/", include_in_schema=False)
        async def serve_admin_index():
            return _admin_index_response()

        @app.get("/admin/{full_path:path}", include_in_schema=False)
        async def serve_admin_spa(full_path: str):
            file_path = _ADMIN_DIST_DIR / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return _admin_index_response()

    # ── Main SPA (/) ───────────────────────────────────────────────────────
    if _DIST_DIR.exists():
        assets_dir = _DIST_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        def _index_response() -> FileResponse:
            r = FileResponse(_DIST_DIR / "index.html", media_type="text/html")
            r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            r.headers["Pragma"] = "no-cache"
            r.headers["Expires"] = "0"
            return r

        def _get_user_from_request(request: "Request") -> "Any | None":
            """Extract and verify the JWT from cookies, return User or None."""
            if not _AUTH_AVAILABLE:
                return None
            try:
                from orchid.auth.jwt import verify_access_token
                from orchid.auth.store import get_store
                token = request.cookies.get("orchid_access")
                if not token:
                    return None
                payload = verify_access_token(token)
                store = get_store()
                return store.get_user(payload.get("sub", ""))
            except Exception:
                return None

        @app.get("/", include_in_schema=False)
        async def serve_index(request: Request):
            user = _get_user_from_request(request)
            if user:
                if user.role == "admin" and _ADMIN_DIST_DIR.exists():
                    return RedirectResponse("/admin/", status_code=302)
                if user.role != "admin" and _PORTAL_DIST_DIR.exists():
                    return RedirectResponse("/app/", status_code=302)
            return _index_response()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            if full_path.startswith("api/") or full_path.startswith("ws/"):
                raise HTTPException(status_code=404)
            file_path = _DIST_DIR / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            index = _DIST_DIR / "index.html"
            if index.exists():
                return _index_response()
            raise HTTPException(status_code=404)
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse({
                "message": "Orchid API running. Frontend not built.",
                "build": "cd orchid/interfaces/web_ui && npm run build",
                "portal_build": "cd orchid/interfaces/portal && npm run build",
            })

    return app


def serve(
    project_paths: list[str],
    host: str = "0.0.0.0",
    port: int = 7842,
    dev: bool = False,
    log_level: str = "info",
    watch_dirs: list[str] | None = None,
    depth: int = 2,
    exclude: list[str] | None = None,
    enable_telegram: bool = False,
    enable_slack: bool = False,
) -> None:
    """Start uvicorn serving the Orchid web app."""
    try:
        import uvicorn
    except ImportError:
        raise ImportError(
            "uvicorn not installed. Run: uv pip install 'uvicorn[standard]>=0.27.0'"
        )

    app = create_app(
        project_paths,
        watch_dirs=watch_dirs,
        depth=depth,
        exclude=exclude,
        enable_telegram=enable_telegram,
        enable_slack=enable_slack,
    )

    reload_dirs = None
    if dev:
        reload_dirs = [str(Path(__file__).parent)]

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        reload=dev,
        reload_dirs=reload_dirs,
    )
