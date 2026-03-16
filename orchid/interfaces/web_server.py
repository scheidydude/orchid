"""FastAPI web server for Orchid.

REST API + WebSocket for real-time agent events.
Serves React frontend from web_ui/dist/.

Architecture (D0027):
- FastAPI with uvicorn for async HTTP + WebSocket
- ConnectionManager per project: thread-safe broadcast to WebSocket clients
- _WebProjectRunner: runs orchestrator in thread pool, emits events to WebSocket

Usage (via CLI):
    orchid web --project <path> [--port 7842] [--host 0.0.0.0]
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

# ── Per-project state ─────────────────────────────────────────────────────────

_projects: dict[str, str] = {}          # project_id → absolute project path
_managers: dict[str, "ConnectionManager"] = {}
_runners: dict[str, "_WebProjectRunner"] = {}
_main_loop: asyncio.AbstractEventLoop | None = None


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


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(project_paths: list[str]) -> Any:
    """Build and return the FastAPI application."""
    if not _FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI not installed. Run: uv pip install 'fastapi>=0.110.0' 'uvicorn[standard]>=0.27.0'"
        )

    _setup_projects(project_paths)

    from orchid import config as cfg_mod
    cors_origins = cfg_mod.get("web.cors_origins", [
        "http://localhost:5173",
        "https://orchid.scheidy.com",
    ])

    @asynccontextmanager
    async def _lifespan(app: FastAPI):  # type: ignore[name-defined]
        global _main_loop
        _main_loop = asyncio.get_running_loop()
        for manager in _managers.values():
            pass  # loop captured above; managers use it on demand
        yield
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

    def _task_dict(t: Any) -> dict[str, Any]:
        return {
            "id": t.id,
            "title": t.title,
            "status": t.status.value,
            "type": t.type,
            "priority": t.priority,
            "description": t.description,
            "depends_on": t.depends_on,
            "model_override": t.model_override,
        }

    # ── REST endpoints ────────────────────────────────────────────────────────

    @app.get("/api/providers")
    async def get_providers():
        from orchid.providers.registry import get_registry
        registry = get_registry()
        return registry.all_status()

    @app.get("/api/projects")
    async def get_projects():
        result = []
        for pid, path in _projects.items():
            try:
                s = _load_session(path)
                todo = sum(1 for t in s.tasks if t.status.value == "TODO")
                done = sum(1 for t in s.tasks if t.status.value == "DONE")
                inprog = sum(1 for t in s.tasks if t.status.value == "IN_PROGRESS")
                blocked = sum(1 for t in s.tasks if t.status.value == "BLOCKED")
                runner = _runners[pid]
                result.append({
                    "id": pid,
                    "name": s.project_name,
                    "path": path,
                    "description": s.project_description,
                    "task_counts": {"todo": todo, "done": done, "in_progress": inprog, "blocked": blocked},
                    "running": runner.is_running(),
                })
            except Exception as exc:
                result.append({"id": pid, "path": path, "error": str(exc)})
        return result

    @app.get("/api/projects/{project_id}/status")
    async def get_project_status(project_id: str):
        path = _get_project(project_id)
        s = _load_session(path)
        runner = _runners[project_id]
        completed_ids = {t.id for t in s.tasks if t.status.value == "DONE"}
        return {
            "id": project_id,
            "name": s.project_name,
            "path": path,
            "description": s.project_description,
            "tasks": [_task_dict(t) for t in s.tasks],
            "decisions": s.decisions[-10:],
            "hot_memory": s.hot_memory,
            "running": runner.is_running(),
            "current_task": runner.current_task,
            "tasks_done": runner.tasks_done,
        }

    @app.get("/api/projects/{project_id}/tasks")
    async def get_tasks(project_id: str):
        path = _get_project(project_id)
        s = _load_session(path)
        return [_task_dict(t) for t in s.tasks]

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
        runner = _runners[project_id]
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
        runner = _runners[project_id]
        if not runner.is_running():
            raise HTTPException(status_code=409, detail="No run in progress")
        runner.stop()
        return {"stopped": True}

    @app.get("/api/projects/{project_id}/run/status")
    async def run_status(project_id: str):
        _get_project(project_id)
        runner = _runners[project_id]
        return {
            "running": runner.is_running(),
            "current_task": runner.current_task,
            "tasks_done": runner.tasks_done,
            "run_id": runner.run_id,
        }

    @app.post("/api/projects/{project_id}/inject")
    async def inject_context(project_id: str, body: dict[str, str]):
        _get_project(project_id)
        runner = _runners[project_id]
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
        # Send current run status immediately on connect
        runner = _runners[project_id]
        await ws.send_json({"type": "connected", "data": {
            "project_id": project_id,
            "running": runner.is_running(),
            "current_task": runner.current_task,
        }})
        try:
            while True:
                # Keep connection alive; receive and ignore client messages
                await ws.receive_text()
        except WebSocketDisconnect:
            manager.remove(ws)

    # ── Frontend static files ─────────────────────────────────────────────────

    if _DIST_DIR.exists():
        # Serve assets with correct caching
        assets_dir = _DIST_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(_DIST_DIR / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            # Don't intercept /api or /ws
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
) -> None:
    """Start uvicorn serving the Orchid web app."""
    try:
        import uvicorn
    except ImportError:
        raise ImportError(
            "uvicorn not installed. Run: uv pip install 'uvicorn[standard]>=0.27.0'"
        )

    app = create_app(project_paths)

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
