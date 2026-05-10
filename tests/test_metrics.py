"""Tests for T085: task metrics capture and default --project to cwd."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from orchid.interfaces.cli import app

runner = CliRunner()

_FINAL_ANSWER = "Final Answer: task complete."


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def orchid_project(tmp_path: Path) -> Path:
    """Minimal orchid project with one pending task."""
    (tmp_path / ".orchid.yaml").write_text("name: test\n", encoding="utf-8")
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n- [ ] **T001** Test task `type:draft` `p1`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Test Project\n", encoding="utf-8")
    return tmp_path


def _run_task(project: Path, fail: bool = False) -> dict:
    """Run single task through real orchestrator path; returns result dict."""
    from orchid.orchestrator import Orchestrator
    from orchid.session import Session

    answer = "[max iterations reached after 1 step]" if fail else _FINAL_ANSWER

    session = Session(project_dir=project)
    session.load()

    with (
        patch("orchid.orchestrator.call", return_value=answer),
        patch("orchid.agents.base.call", return_value=answer),
        patch("orchid.providers.registry.ProviderRegistry.resolve_name", return_value="local"),
    ):
        orch = Orchestrator(session)
        result = orch.run_once()

    session.save()
    return result or {}


# ── Default --project to cwd ──────────────────────────────────────────────────


def test_default_project_cwd(orchid_project: Path) -> None:
    """orchid --status without --project should use cwd if it has .orchid.yaml."""
    original_cwd = os.getcwd()
    try:
        os.chdir(orchid_project)
        result = runner.invoke(app, ["--status"])
        # Should not error with "No .orchid.yaml found"
        assert "No .orchid.yaml found" not in result.output
        assert result.exit_code == 0
    finally:
        os.chdir(original_cwd)


def test_default_project_cwd_no_orchid_yaml_shows_error(tmp_path: Path) -> None:
    """orchid --status without --project in a non-orchid dir should show an error."""
    # tmp_path has no .orchid.yaml
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["--status"])
        assert result.exit_code == 1
        assert ".orchid.yaml" in result.output
    finally:
        os.chdir(original_cwd)


# ── Task metrics written on completion ────────────────────────────────────────


def test_task_metrics_written_on_completion(orchid_project: Path) -> None:
    """On successful task completion, a metrics record is appended to task_metrics.jsonl."""
    _run_task(orchid_project, fail=False)

    metrics_file = orchid_project / ".orchid" / "task_metrics.jsonl"
    assert metrics_file.exists(), "task_metrics.jsonl should be created"

    records = [json.loads(line) for line in metrics_file.read_text().splitlines() if line.strip()]
    assert len(records) >= 1

    rec = records[0]
    assert rec["task_id"] == "T001"
    assert rec["status"] == "done"
    assert "duration_s" in rec
    assert "iters_used" in rec
    assert "iters_max" in rec
    assert "action_counts" in rec
    assert "model" in rec
    assert "session_id" in rec
    assert "timestamp" in rec
    assert "blocker" not in rec  # no blocker on success


def test_task_metrics_written_on_blocked(orchid_project: Path) -> None:
    """On blocked/failed task, a metrics record with blocker field is written."""
    _run_task(orchid_project, fail=True)

    metrics_file = orchid_project / ".orchid" / "task_metrics.jsonl"
    assert metrics_file.exists(), "task_metrics.jsonl should be created even on failure"

    records = [json.loads(line) for line in metrics_file.read_text().splitlines() if line.strip()]
    assert len(records) >= 1

    rec = records[0]
    assert rec["task_id"] == "T001"
    assert rec["status"] == "blocked"
    assert "blocker" in rec
    blocker = rec["blocker"]
    assert "reason" in blocker
    assert "last_action" in blocker
    assert "last_error" in blocker


# ── Web API metrics endpoint ──────────────────────────────────────────────────


def test_task_metrics_endpoint_returns_data(tmp_path: Path) -> None:
    """GET /api/projects/{id}/metrics returns parsed records from task_metrics.jsonl."""
    from fastapi.testclient import TestClient

    import orchid.interfaces.web_server as ws

    # Reset module-level state
    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()

    (tmp_path / "tasks.md").write_text("# Tasks\n\n## TODO\n\n## DONE\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Hot memory\n", encoding="utf-8")

    # Pre-populate a metrics file
    orchid_dir = tmp_path / ".orchid"
    orchid_dir.mkdir()
    metrics_file = orchid_dir / "task_metrics.jsonl"
    metrics_file.write_text(
        json.dumps(
            {
                "task_id": "T001",
                "title": "Test task",
                "status": "done",
                "iters_used": 3,
                "iters_max": 15,
                "duration_s": 1.5,
                "action_counts": {"read_file": 2},
                "model": "local",
                "session_id": "session_20260325_120000",
                "timestamp": "2026-03-25T12:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    app = ws.create_app([str(tmp_path)])
    project_id = tmp_path.name

    with TestClient(app) as client:
        resp = client.get(f"/api/projects/{project_id}/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["task_id"] == "T001"
        assert data[0]["status"] == "done"
        assert data[0]["duration_s"] == 1.5


def test_task_metrics_endpoint_empty_when_no_file(tmp_path: Path) -> None:
    """GET /api/projects/{id}/metrics returns empty list when no metrics file exists."""
    from fastapi.testclient import TestClient

    import orchid.interfaces.web_server as ws

    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()

    (tmp_path / "tasks.md").write_text("# Tasks\n\n## TODO\n\n## DONE\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Hot memory\n", encoding="utf-8")

    app = ws.create_app([str(tmp_path)])
    project_id = tmp_path.name

    with TestClient(app) as client:
        resp = client.get(f"/api/projects/{project_id}/metrics")
        assert resp.status_code == 200
        assert resp.json() == []
