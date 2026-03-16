"""Tests for the Orchid web server API endpoints and WebSocket.

Focus: API contract — not live agent runs or browser tests.
Uses FastAPI TestClient (sync) and httpx async client for WebSocket tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_task(tid="T001", title="Test task", status="TODO", ttype="draft", priority=2,
               depends_on=None, model_override=None, description=""):
    t = SimpleNamespace()
    t.id = tid
    t.title = title
    t.status = SimpleNamespace(value=status)
    t.type = ttype
    t.priority = priority
    t.description = description
    t.depends_on = depends_on or []
    t.model_override = model_override
    t.is_runnable = lambda completed: all(d in completed for d in t.depends_on)
    return t


def _make_session(name="TestProject", description="A test", tasks=None, hot_memory="# Hot memory"):
    s = SimpleNamespace()
    s.project_name = name
    s.project_description = description
    s.tasks = tasks or []
    s.hot_memory = hot_memory
    s.decisions = []
    s.delegations = []
    return s


def _make_app(tmp_path: Path):
    """Build a test app with a single project pointing at tmp_path."""
    # Reset module-level state
    import orchid.interfaces.web_server as ws
    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()

    project_path = str(tmp_path)
    # Create minimal tasks.md so Session.load() doesn't crash
    (tmp_path / "tasks.md").write_text("# Tasks\n\n## TODO\n\n## DONE\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Hot memory\nProject notes.", encoding="utf-8")

    app = ws.create_app([project_path])
    return app, tmp_path.name  # project_id = dirname


def _patch_session(session):
    """Context manager that patches Session.load() to return a fake session."""
    from unittest.mock import patch as _patch

    class _FakeSession:
        def __init__(self, project_dir):
            self.project_dir = Path(project_dir)
            self.project_name = session.project_name
            self.project_description = session.project_description
            self.tasks = session.tasks
            self.hot_memory = session.hot_memory
            self.decisions = session.decisions

        def load(self):
            pass

        def update_task_status(self, task_id, new_status):
            for t in self.tasks:
                if t.id == task_id:
                    t.status = SimpleNamespace(value=new_status.value)
                    return True
            return False

    return _patch("orchid.interfaces.web_server.Session", _FakeSession)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path):
    (tmp_path / "tasks.md").write_text("# Tasks\n\n## TODO\n\n## DONE\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Hot memory\nProject notes here.", encoding="utf-8")
    (tmp_path / ".orchid").mkdir()
    return tmp_path


@pytest.fixture
def client(project_dir):
    """TestClient with a single project, Session patched to return a fake session."""
    import orchid.interfaces.web_server as ws
    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()

    tasks = [_make_task("T001", "Build auth", "TODO"), _make_task("T002", "Write tests", "DONE")]
    session = _make_session(tasks=tasks)

    with patch("orchid.session.Session") as MockSession:
        instance = MockSession.return_value
        instance.project_name = session.project_name
        instance.project_description = session.project_description
        instance.tasks = session.tasks
        instance.hot_memory = session.hot_memory
        instance.decisions = []
        instance.load.return_value = None
        instance.update_task_status.return_value = True

        app = ws.create_app([str(project_dir)])
        client = TestClient(app, raise_server_exceptions=True)
        client._project_id = project_dir.name
        yield client, project_dir.name


# ── Project list ──────────────────────────────────────────────────────────────


def test_get_projects_returns_list(client):
    c, pid = client
    response = c.get("/api/projects")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(p["id"] == pid for p in data)


def test_get_projects_has_task_counts(client):
    c, pid = client
    response = c.get("/api/projects")
    data = response.json()
    project = next(p for p in data if p["id"] == pid)
    assert "task_counts" in project
    counts = project["task_counts"]
    assert "todo" in counts and "done" in counts


# ── Project status ────────────────────────────────────────────────────────────


def test_get_project_status(client):
    c, pid = client
    response = c.get(f"/api/projects/{pid}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == pid
    assert "tasks" in data
    assert "hot_memory" in data
    assert "running" in data


def test_get_project_status_404(client):
    c, _ = client
    response = c.get("/api/projects/nonexistent/status")
    assert response.status_code == 404


# ── Tasks ─────────────────────────────────────────────────────────────────────


def test_get_tasks(client):
    c, pid = client
    response = c.get(f"/api/projects/{pid}/tasks")
    assert response.status_code == 200
    tasks = response.json()
    assert isinstance(tasks, list)


def test_post_task_creates_task(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    with patch("orchid.session.Session") as MockSession:
        instance = MockSession.return_value
        instance.project_name = "Test"
        instance.project_description = ""
        instance.tasks = []
        instance.hot_memory = ""
        instance.decisions = []
        instance.load.return_value = None

        with patch("orchid.memory.state.save_tasks") as mock_save:
            response = c.post(
                f"/api/projects/{pid}/tasks",
                json={"title": "New Task", "type": "code_generate", "priority": 1},
            )

    assert response.status_code == 201
    task = response.json()
    assert task["title"] == "New Task"
    assert task["type"] == "code_generate"
    assert task["priority"] == 1
    assert task["id"].startswith("T")


def test_patch_task_updates_status(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    task = _make_task("T001", "Fix bug", "TODO")

    with patch("orchid.session.Session") as MockSession:
        instance = MockSession.return_value
        instance.tasks = [task]
        instance.load.return_value = None

        def _update(tid, status):
            for t in instance.tasks:
                if t.id == tid:
                    from orchid.memory.state import TaskStatus
                    t.status = SimpleNamespace(value=status.value)
                    return True
            return False
        instance.update_task_status.side_effect = _update

        with patch("orchid.memory.state.save_tasks"):
            response = c.patch(
                f"/api/projects/{pid}/tasks/T001",
                json={"status": "done"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "DONE"


def test_patch_task_invalid_status(client):
    c, pid = client
    response = c.patch(
        f"/api/projects/{pid}/tasks/T001",
        json={"status": "invalid_status"},
    )
    assert response.status_code == 400


# ── Decisions ─────────────────────────────────────────────────────────────────


def test_get_decisions(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    decisions_path = project_dir / ".orchid" / "decisions.json"
    decisions_path.parent.mkdir(exist_ok=True)
    record = {"id": "D0001", "title": "Use FastAPI", "decision": "FastAPI for REST", "rationale": "Async", "timestamp": "2026-03-15T00:00:00+00:00"}
    decisions_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    response = c.get(f"/api/projects/{pid}/decisions")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert any(d["id"] == "D0001" for d in data)


# ── Sessions ──────────────────────────────────────────────────────────────────


def test_get_sessions(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    log_dir = project_dir / ".orchid" / "session_logs"
    log_dir.mkdir(parents=True)
    (log_dir / "session_20260315_120000.jsonl").write_text(
        json.dumps({"type": "session_start", "timestamp": "2026-03-15T12:00:00Z"}) + "\n",
        encoding="utf-8",
    )

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    response = c.get(f"/api/projects/{pid}/sessions")
    assert response.status_code == 200
    sessions = response.json()
    assert isinstance(sessions, list)
    assert len(sessions) >= 1
    assert sessions[0]["id"] == "session_20260315_120000"


# ── Recall ────────────────────────────────────────────────────────────────────


def test_post_recall(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    fake_results = [{"text": "session summary", "distance": 0.1, "metadata": {"type": "session"}}]
    with (
        patch("orchid.memory.vector.VectorMemory") as MockVM,
        patch("orchid.config.configure_for_project"),
    ):
        instance = MockVM.return_value
        instance.available = True
        instance.query.return_value = fake_results
        response = c.post(
            f"/api/projects/{pid}/recall",
            json={"query": "auth module", "n": 3},
        )

    assert response.status_code == 200
    assert response.json() == fake_results


# ── Run lifecycle ─────────────────────────────────────────────────────────────


def test_post_run_starts_background(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    runner = ws._runners[pid]
    with patch.object(runner, "start", return_value="20260315_120000") as mock_start:
        response = c.post(f"/api/projects/{pid}/run", json={"mode": "auto"})

    assert response.status_code == 200
    assert response.json()["run_id"] == "20260315_120000"
    mock_start.assert_called_once()


def test_post_run_409_when_already_running(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    runner = ws._runners[pid]
    with patch.object(runner, "is_running", return_value=True):
        response = c.post(f"/api/projects/{pid}/run", json={"mode": "auto"})

    assert response.status_code == 409


def test_delete_run_stops_background(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    runner = ws._runners[pid]
    with (
        patch.object(runner, "is_running", return_value=True),
        patch.object(runner, "stop") as mock_stop,
    ):
        response = c.delete(f"/api/projects/{pid}/run")

    assert response.status_code == 200
    mock_stop.assert_called_once()


def test_delete_run_409_when_not_running(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    response = c.delete(f"/api/projects/{pid}/run")
    assert response.status_code == 409


# ── WebSocket ─────────────────────────────────────────────────────────────────


def test_websocket_connects(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name

    with c.websocket_connect(f"/ws/{pid}") as wsconn:
        msg = wsconn.receive_json()
        assert msg["type"] == "connected"
        assert msg["data"]["project_id"] == pid


def test_websocket_receives_events(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)
    pid = project_dir.name
    manager = ws._managers[pid]

    with c.websocket_connect(f"/ws/{pid}") as wsconn:
        # Consume the initial 'connected' message
        wsconn.receive_json()

        # Broadcast a test event from "background thread" (synchronous in test)
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(manager.broadcast({"type": "task_start", "data": {"task_id": "T001", "title": "Test"}}))
        finally:
            loop.close()

        msg = wsconn.receive_json()
        assert msg["type"] == "task_start"
        assert msg["data"]["task_id"] == "T001"


def test_websocket_unknown_project_closes(project_dir):
    import orchid.interfaces.web_server as ws
    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()

    app = ws.create_app([str(project_dir)])
    c = TestClient(app)

    with pytest.raises(Exception):
        with c.websocket_connect("/ws/nonexistent_project_xyz") as wsconn:
            wsconn.receive_json()
