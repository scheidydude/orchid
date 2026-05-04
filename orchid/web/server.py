import asyncio
from pathlib import Path
from collections import deque
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from orchid.registry import ProjectRegistry

from orchid.planning import PlanningSession
from orchid.runner import BackgroundRunner

app = FastAPI()

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

registry = ProjectRegistry()
runner = BackgroundRunner()


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