"""Tests for V2 web API endpoints — lifecycle, discussion, artifacts, project creation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def proj(tmp_path):
    """Minimal project directory with required scaffold files."""
    (tmp_path / ".orchid").mkdir()
    (tmp_path / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / ".orchid.yaml").write_text("project: test\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def app_client(proj):
    """TestClient with a single project registered."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import orchid.interfaces.web_server as ws

    # Reset module-level state
    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()
    ws._main_loop = None

    ws._setup_projects([str(proj)])
    app = ws.create_app([str(proj)])
    return TestClient(app), proj.name


# ── GET /api/projects/{id}/lifecycle ─────────────────────────────────────────


def test_get_lifecycle_returns_phase(app_client):
    client, pid = app_client
    r = client.get(f"/api/projects/{pid}/lifecycle")
    assert r.status_code == 200
    data = r.json()
    assert "phase" in data
    assert data["phase"] in ("NEW", "DISCUSSING", "REQUIREMENTS", "PLANNING", "READY", "EXECUTING", "COMPLETE")


def test_get_lifecycle_returns_artifacts_map(app_client):
    client, pid = app_client
    r = client.get(f"/api/projects/{pid}/lifecycle")
    data = r.json()
    assert "artifacts" in data
    assert "REQUIREMENTS.md" in data["artifacts"]
    assert "tasks.md" in data["artifacts"]


def test_get_lifecycle_404_for_unknown(app_client):
    client, _ = app_client
    r = client.get("/api/projects/nonexistent/lifecycle")
    assert r.status_code == 404


# ── GET /api/projects/{id}/discussion ────────────────────────────────────────


def test_get_discussion_empty(app_client):
    client, pid = app_client
    r = client.get(f"/api/projects/{pid}/discussion")
    assert r.status_code == 200
    data = r.json()
    assert "turns" in data
    assert "turn_count" in data
    assert isinstance(data["turns"], list)


def test_get_discussion_returns_history(app_client, proj):
    from orchid.discussion import DiscussionHistory
    h = DiscussionHistory.load(proj)
    h.append("user", "hello")
    h.append("agent", "hi there")

    client, pid = app_client
    r = client.get(f"/api/projects/{pid}/discussion")
    data = r.json()
    assert data["turn_count"] == 2


# ── POST /api/projects/{id}/discussion ───────────────────────────────────────


def test_post_discussion_returns_response(app_client):
    client, pid = app_client

    mock_response = MagicMock()
    mock_response.message = "What features do you need?"
    mock_response.ready_to_advance = False
    mock_response.suggestions = ["Tell me about auth"]
    mock_response.context_updates = ""

    with patch("orchid.agents.discussion_agent.DiscussionAgent.run", return_value=mock_response):
        r = client.post(f"/api/projects/{pid}/discussion", json={"message": "I want a recipe app"})

    assert r.status_code == 200
    data = r.json()
    assert "response" in data
    assert "ready_to_advance" in data
    assert "suggestions" in data


def test_post_discussion_saves_to_history(app_client, proj):
    client, pid = app_client

    mock_response = MagicMock()
    mock_response.message = "Interesting!"
    mock_response.ready_to_advance = False
    mock_response.suggestions = []
    mock_response.context_updates = ""

    with patch("orchid.agents.discussion_agent.DiscussionAgent.run", return_value=mock_response):
        client.post(f"/api/projects/{pid}/discussion", json={"message": "Build a todo app"})

    from orchid.discussion import DiscussionHistory
    h = DiscussionHistory.load(proj)
    assert h.turn_count() >= 1


# ── POST /api/projects/{id}/advance ──────────────────────────────────────────


def test_advance_confirm_false_returns_phase(app_client):
    client, pid = app_client
    r = client.post(f"/api/projects/{pid}/advance", json={"confirm": False})
    assert r.status_code == 200
    data = r.json()
    assert "phase" in data


def test_advance_triggers_pm_agent(app_client, proj):
    from orchid.discussion import DiscussionHistory
    h = DiscussionHistory.load(proj)
    h.append("user", "I want a recipe app")

    client, pid = app_client

    pm_result = MagicMock()
    pm_result.requirements_path = proj / "REQUIREMENTS.md"
    pm_result.architecture_path = proj / "ARCHITECTURE.md"

    pmgr_result = MagicMock()
    pmgr_result.milestones_path = proj / "MILESTONES.md"
    pmgr_result.tasks_path = proj / "tasks.md"
    pmgr_result.task_count = 10

    with patch("orchid.agents.product_manager.ProductManagerAgent.run", return_value=pm_result) as pm_mock, \
         patch("orchid.agents.project_manager.ProjectManagerAgent.run", return_value=pmgr_result) as pmgr_mock:
        # Create dummy files so lifecycle advances work
        (proj / "REQUIREMENTS.md").write_text("# Req", encoding="utf-8")
        (proj / "ARCHITECTURE.md").write_text("# Arch", encoding="utf-8")
        (proj / "MILESTONES.md").write_text("# Milestones", encoding="utf-8")

        r = client.post(f"/api/projects/{pid}/advance", json={"confirm": True})

    assert r.status_code == 200
    data = r.json()
    assert "phase" in data
    pm_mock.assert_called_once()


# ── POST /api/projects/{id}/approve ──────────────────────────────────────────


def test_approve_gate_updates_phase(app_client, proj):
    from orchid.lifecycle import ProjectLifecycle
    lc = ProjectLifecycle.load(proj)
    lc.advance("DISCUSSING")

    # Create artifacts so gate isn't BLOCKED
    (proj / "REQUIREMENTS.md").write_text("# Req")
    (proj / "ARCHITECTURE.md").write_text("# Arch")
    (proj / "MILESTONES.md").write_text("# M")
    lc.advance("REQUIREMENTS")
    lc.advance("PLANNING")
    lc.advance("READY")

    client, pid = app_client
    r = client.post(f"/api/projects/{pid}/approve", json={"auto_future": False})
    assert r.status_code == 200
    data = r.json()
    assert "phase" in data
    assert data["phase"] == "EXECUTING"


def test_approve_gate_404_for_unknown(app_client):
    client, _ = app_client
    r = client.post("/api/projects/ghost/approve", json={})
    assert r.status_code == 404


# ── GET /api/projects/{id}/artifacts ─────────────────────────────────────────


def test_get_artifacts_returns_content(app_client, proj):
    (proj / "REQUIREMENTS.md").write_text("# Requirements\nFR-001: Login", encoding="utf-8")
    client, pid = app_client
    r = client.get(f"/api/projects/{pid}/artifacts")
    assert r.status_code == 200
    data = r.json()
    assert "requirements" in data
    assert data["requirements"]["exists"] is True
    assert "FR-001" in data["requirements"]["content"]


def test_get_artifacts_missing_returns_false(app_client):
    client, pid = app_client
    r = client.get(f"/api/projects/{pid}/artifacts")
    data = r.json()
    assert data["requirements"]["exists"] is False
    assert data["requirements"]["content"] is None


def test_save_artifact_updates_file(app_client, proj):
    client, pid = app_client
    r = client.patch(f"/api/projects/{pid}/artifacts/requirements",
                     json={"content": "# Updated Requirements\n"})
    assert r.status_code == 200
    assert (proj / "REQUIREMENTS.md").read_text() == "# Updated Requirements\n"


# ── POST /api/projects (create) ───────────────────────────────────────────────


def test_post_projects_confirm_path_false_returns_suggestion(app_client):
    client, _ = app_client
    with patch("orchid.machine_profile.MachineProfile.load") as mock_load:
        profile = MagicMock()
        profile.get_project_root.return_value = Path("/tmp/projects")
        profile.defaults = {"git_init": False}
        profile.project_roots = {"default": "/tmp/projects", "type_routing": {}}
        profile.preferred_stacks = {}
        profile.infrastructure = {}
        mock_load.return_value = profile
        r = client.post("/api/projects", json={
            "name": "testapp",
            "description": "A test",
            "confirm_path": False,
        })
    assert r.status_code == 201
    data = r.json()
    assert "suggested_path" in data
    assert "testapp" in data["suggested_path"]


def test_post_projects_creates_project(app_client, tmp_path):
    client, _ = app_client

    def _fake_create(**kwargs):
        proj_dir = tmp_path / "newapp"
        proj_dir.mkdir()
        (proj_dir / ".orchid").mkdir()
        (proj_dir / "tasks.md").write_text("# Tasks\n")
        (proj_dir / "CLAUDE.md").write_text("# newapp\n")
        (proj_dir / ".orchid.yaml").write_text("project: newapp\n")
        return proj_dir

    with patch("orchid.project_creator.ProjectCreator.create", side_effect=_fake_create):
        r = client.post("/api/projects", json={
            "name": "newapp",
            "description": "A new project",
            "confirm_path": True,
        })

    assert r.status_code == 201
    data = r.json()
    assert "project_id" in data
    assert "path" in data


# ── GET /api/machine-profile ─────────────────────────────────────────────────


def test_get_machine_profile_returns_redacted(app_client):
    client, _ = app_client
    with patch("orchid.machine_profile.MachineProfile.load") as mock_load:
        profile = MagicMock()
        profile.developer_name = "Dave"
        profile.project_roots = {"default": "~/Projects"}
        profile.preferred_stacks = {"backend": {"primary": "fastapi"}}
        profile.infrastructure = {
            "local_llm": {"base_url": "http://localhost:8080"},
            "reverse_proxy": "traefik",
        }
        profile.defaults = {"git_init": True}
        mock_load.return_value = profile

        r = client.get("/api/machine-profile")

    assert r.status_code == 200
    data = r.json()
    assert data["developer_name"] == "Dave"
    # local_llm should be redacted
    assert "local_llm" not in data.get("infrastructure", {})
    assert "reverse_proxy" in data.get("infrastructure", {})


# ── WebSocket /ws/{id}/discussion ─────────────────────────────────────────────


def test_websocket_discussion_streams_tokens(app_client):
    client, pid = app_client

    mock_response = MagicMock()
    mock_response.message = "Tell me more!"
    mock_response.ready_to_advance = False
    mock_response.suggestions = []
    mock_response.context_updates = ""

    with patch("orchid.agents.discussion_agent.DiscussionAgent.run", return_value=mock_response):
        with client.websocket_connect(f"/ws/{pid}/discussion") as ws:
            ws.send_json({"message": "hello"})
            msgs = []
            for _ in range(3):
                try:
                    msg = ws.receive_json()
                    msgs.append(msg)
                    if msg.get("type") == "done":
                        break
                except Exception:
                    break

    types = [m.get("type") for m in msgs]
    assert "done" in types or "token" in types or "thinking" in types
