"""Tests for T084 PM Dashboard — backend API used by the dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_task(tid, title="Task", status="TODO", ttype="draft", priority=2, depends_on=None):
    t = SimpleNamespace()
    t.id = tid
    t.title = title
    t.status = SimpleNamespace(value=status)
    t.type = ttype
    t.priority = priority
    t.description = ""
    t.depends_on = depends_on or []
    t.model_override = None
    t.is_runnable = lambda completed: all(d in completed for d in t.depends_on)
    return t


def _setup_app(tmp_path: Path, tasks=None):
    import orchid.interfaces.web_server as ws
    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()

    (tmp_path / "tasks.md").write_text("# Tasks\n\n## TODO\n\n## DONE\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Hot memory\n", encoding="utf-8")

    app = ws.create_app([str(tmp_path)])
    return TestClient(app), tmp_path.name


# ── Metrics endpoint ──────────────────────────────────────────────────────────


def test_metrics_endpoint_returns_task_data(tmp_path: Path) -> None:
    """GET /api/projects/{id}/metrics returns records from task_metrics.jsonl."""
    orchid_dir = tmp_path / ".orchid"
    orchid_dir.mkdir()
    records = [
        {
            "task_id": "T001", "title": "Build auth", "status": "done",
            "iters_used": 4, "iters_max": 15, "duration_s": 12.3,
            "action_counts": {"read_file": 3, "write_file": 1},
            "model": "local", "session_id": "session_20260325_120000",
            "timestamp": "2026-03-25T12:00:00+00:00",
        },
        {
            "task_id": "T002", "title": "Write tests", "status": "blocked",
            "iters_used": 15, "iters_max": 15, "duration_s": 45.1,
            "action_counts": {"bash": 5},
            "model": "claude", "session_id": "session_20260325_130000",
            "timestamp": "2026-03-25T13:00:00+00:00",
            "blocker": {"reason": "[max iterations reached]", "last_action": "bash", "last_error": ""},
        },
    ]
    (orchid_dir / "task_metrics.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )

    client, pid = _setup_app(tmp_path)
    with client:
        resp = client.get(f"/api/projects/{pid}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["task_id"] == "T001"
    assert data[0]["status"] == "done"
    assert data[1]["task_id"] == "T002"
    assert data[1]["status"] == "blocked"
    assert "blocker" in data[1]
    assert data[1]["blocker"]["last_action"] == "bash"


def test_metrics_endpoint_empty_when_no_tasks_run(tmp_path: Path) -> None:
    """GET /api/projects/{id}/metrics returns [] when no metrics file exists."""
    client, pid = _setup_app(tmp_path)
    with client:
        resp = client.get(f"/api/projects/{pid}/metrics")
    assert resp.status_code == 200
    assert resp.json() == []


def test_metrics_endpoint_skips_malformed_lines(tmp_path: Path) -> None:
    """Malformed JSONL lines are skipped gracefully."""
    orchid_dir = tmp_path / ".orchid"
    orchid_dir.mkdir()
    (orchid_dir / "task_metrics.jsonl").write_text(
        '{"task_id": "T001", "status": "done"}\n'
        'not valid json\n'
        '{"task_id": "T002", "status": "done"}\n',
        encoding="utf-8",
    )
    client, pid = _setup_app(tmp_path)
    with client:
        resp = client.get(f"/api/projects/{pid}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert {r["task_id"] for r in data} == {"T001", "T002"}


# ── Milestone grouping logic (pure Python) ────────────────────────────────────


def _group_by_milestone(tasks):
    """Mirror of the JS groupByMilestone logic for testing the concept."""
    rollups = [t for t in tasks if t.type == "rollup"]
    if not rollups:
        return [{"name": "All Tasks", "tasks": tasks}]

    milestones = []
    assigned = set()
    for rollup in rollups:
        sources = rollup.depends_on or []
        members = [t for t in tasks if t.id in sources]
        for t in members:
            assigned.add(t.id)
        milestones.append({"name": rollup.title, "tasks": members})

    unassigned = [t for t in tasks if t.id not in assigned and t.type != "rollup"]
    if unassigned:
        milestones.insert(0, {"name": "Unassigned", "tasks": unassigned})

    return milestones


def test_milestone_grouping_from_rollup_tasks() -> None:
    """Rollup tasks act as milestone markers; non-rollup tasks assigned to milestones."""
    tasks = [
        _make_task("T001", "Task A", ttype="draft"),
        _make_task("T002", "Task B", ttype="draft"),
        _make_task("T003", "Task C", ttype="draft"),
        _make_task("T099", "Sprint 1 Rollup", ttype="rollup", depends_on=["T001", "T002"]),
    ]
    milestones = _group_by_milestone(tasks)
    assert len(milestones) == 2  # "Unassigned" + "Sprint 1 Rollup"

    names = {m["name"] for m in milestones}
    assert "Sprint 1 Rollup" in names
    assert "Unassigned" in names

    sprint1 = next(m for m in milestones if m["name"] == "Sprint 1 Rollup")
    assert len(sprint1["tasks"]) == 2
    assert {t.id for t in sprint1["tasks"]} == {"T001", "T002"}

    unassigned = next(m for m in milestones if m["name"] == "Unassigned")
    assert len(unassigned["tasks"]) == 1
    assert unassigned["tasks"][0].id == "T003"


def test_milestone_grouping_no_rollups() -> None:
    """When no rollup tasks exist all tasks group into 'All Tasks'."""
    tasks = [_make_task("T001"), _make_task("T002"), _make_task("T003")]
    milestones = _group_by_milestone(tasks)
    assert len(milestones) == 1
    assert milestones[0]["name"] == "All Tasks"
    assert len(milestones[0]["tasks"]) == 3


def test_milestone_grouping_multiple_rollups() -> None:
    """Multiple rollup tasks each become their own milestone."""
    tasks = [
        _make_task("T001", ttype="draft"),
        _make_task("T002", ttype="draft"),
        _make_task("T003", ttype="draft"),
        _make_task("T004", ttype="draft"),
        _make_task("T090", "Sprint 1", ttype="rollup", depends_on=["T001", "T002"]),
        _make_task("T091", "Sprint 2", ttype="rollup", depends_on=["T003", "T004"]),
    ]
    milestones = _group_by_milestone(tasks)
    milestone_names = [m["name"] for m in milestones]
    assert "Sprint 1" in milestone_names
    assert "Sprint 2" in milestone_names
    sprint1 = next(m for m in milestones if m["name"] == "Sprint 1")
    sprint2 = next(m for m in milestones if m["name"] == "Sprint 2")
    assert {t.id for t in sprint1["tasks"]} == {"T001", "T002"}
    assert {t.id for t in sprint2["tasks"]} == {"T003", "T004"}
