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
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

_DIST_DIR = Path(__file__).parent / "web_ui" / "dist"

# ── Module-level state ─────────────────────────────────────────────────────────

_projects: dict[str, str] = {}           # project_id → absolute project path
_managers: dict[str, "ConnectionManager"] = {}
_runners: dict[str, "_WebProjectRunner"] = {}
_main_loop: asyncio.AbstractEventLoop | None = None
_state_lock = threading.Lock()           # protects _projects/_managers/_runners

# Optional discovery instance (set by create_app when watch_dirs provided)
_discovery: Any | None = None
_agent_manager: Any | None = None


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
        with self._lock:
            conns = list(self._connections)
        dead = []
        for ws in conns:
            try:
                await ws.send_json(data)
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

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        mode: str = "auto",
        code_model: str | None = None,
    ) -> str:
        with self._lock:
            if self.is_running():
                return ""
            rid = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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
        from orchid.session import Session
        from orchid.orchestrator import Orchestrator
        from orchid.memory.state import TaskStatus

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
        model: Optional[str] = None
        description: str = ""

    class PatchTaskBody(BaseModel):
        status: str  # done | blocked | cancelled

    class RunBody(BaseModel):
        mode: str = "auto"
        code_model: Optional[str] = None

    class RecallBody(BaseModel):
        query: str
        n: int = 5

    class SearchBody(BaseModel):
        query: str


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
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(
    project_paths: list[str],
    watch_dirs: list[str] | None = None,
    depth: int = 2,
    exclude: list[str] | None = None,
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
    global _discovery, _agent_manager

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

            _discovery.watch(_on_discovery_change, on_removed=_on_project_removed)

        yield

        # Shutdown
        if _discovery is not None:
            _discovery.stop()
        if _agent_manager is not None:
            _agent_manager.stop()
        for runner in _runners.values():
            runner.stop()

    app = FastAPI(title="Orchid", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
                todo = sum(1 for t in s.tasks if t.status.value == "TODO")
                done = sum(1 for t in s.tasks if t.status.value == "DONE")
                inprog = sum(1 for t in s.tasks if t.status.value == "IN_PROGRESS")
                blocked = sum(1 for t in s.tasks if t.status.value == "BLOCKED")
                runner = _runners.get(pid)
                persistent = _read_persistent_config(path)
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
            "last_scan": datetime.now(timezone.utc).isoformat(),
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
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
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
        return {
            "running": runner.is_running(),
            "current_task": runner.current_task,
            "tasks_done": runner.tasks_done,
            "run_id": runner.run_id,
        }

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
        try:
            while True:
                # Keep connection alive; receive and ignore client messages
                await ws.receive_text()
        except WebSocketDisconnect:
            manager.remove(ws)

    # ── Frontend static files ─────────────────────────────────────────────────

    if _DIST_DIR.exists():
        assets_dir = _DIST_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(_DIST_DIR / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            if full_path.startswith("api/") or full_path.startswith("ws/"):
                raise HTTPException(status_code=404)
            file_path = _DIST_DIR / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            index = _DIST_DIR / "index.html"
            if index.exists():
                return FileResponse(index)
            raise HTTPException(status_code=404)
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse({
                "message": "Orchid API running. Frontend not built.",
                "build": "cd orchid/interfaces/web_ui && npm run build",
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
    )

    reload_dirs = None
    if dev:
        reload_dirs = [str(Path(__file__).parent)]

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        reload=dev,
        reload_dirs=reload_dirs,
    )
