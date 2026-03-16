"""Tests for ProjectDiscovery and AgentManager."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orchid.discovery import ProjectDiscovery
from orchid.agent_manager import AgentManager, ProjectConfig


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_project(tmp_path: Path, name: str, with_yaml: bool = True) -> Path:
    """Create a minimal orchid project directory."""
    proj = tmp_path / name
    proj.mkdir()
    if with_yaml:
        (proj / ".orchid.yaml").write_text(f"project: {name}\n", encoding="utf-8")
        (proj / "CLAUDE.md").write_text(f"# {name}\n", encoding="utf-8")
        (proj / "tasks.md").write_text("# Tasks\n\n## TODO\n\n## DONE\n", encoding="utf-8")
    return proj


# ── ProjectDiscovery tests ────────────────────────────────────────────────────


def test_scan_finds_orchid_projects(tmp_path):
    _make_project(tmp_path, "alpha")
    _make_project(tmp_path, "beta")
    _make_project(tmp_path, "not-a-project", with_yaml=False)

    d = ProjectDiscovery(watch_dirs=[tmp_path])
    found = d.scan()

    names = {p.name for p in found}
    assert "alpha" in names
    assert "beta" in names
    assert "not-a-project" not in names


def test_scan_ignores_excluded_dirs(tmp_path):
    # venv inside watch dir should be skipped
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / ".orchid.yaml").write_text("project: hidden\n", encoding="utf-8")

    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / ".orchid.yaml").write_text("project: hidden2\n", encoding="utf-8")

    d = ProjectDiscovery(watch_dirs=[tmp_path])
    found = d.scan()

    names = {p.name for p in found}
    assert ".venv" not in names
    assert "node_modules" not in names


def test_scan_respects_depth_limit(tmp_path):
    # depth=1: projects at level 1 are found, level 2 are not
    level1 = _make_project(tmp_path, "level1")
    level2_dir = tmp_path / "container"
    level2_dir.mkdir()
    level2 = level2_dir / "level2"
    level2.mkdir()
    (level2 / ".orchid.yaml").write_text("project: level2\n", encoding="utf-8")

    d = ProjectDiscovery(watch_dirs=[tmp_path], depth=1)
    found = d.scan()
    names = {p.name for p in found}

    assert "level1" in names
    assert "level2" not in names


def test_scan_respects_depth_limit_finds_deeper(tmp_path):
    # depth=2 (default): projects at level 2 are found
    level1_dir = tmp_path / "container"
    level1_dir.mkdir()
    level2 = level1_dir / "level2proj"
    level2.mkdir()
    (level2 / ".orchid.yaml").write_text("project: level2proj\n", encoding="utf-8")

    d = ProjectDiscovery(watch_dirs=[tmp_path], depth=2)
    found = d.scan()
    names = {p.name for p in found}

    assert "level2proj" in names


def test_is_orchid_project_true(tmp_path):
    proj = _make_project(tmp_path, "mything")
    d = ProjectDiscovery(watch_dirs=[])
    assert d.is_orchid_project(proj) is True


def test_is_orchid_project_false(tmp_path):
    d = ProjectDiscovery(watch_dirs=[])
    assert d.is_orchid_project(tmp_path) is False


def test_discovery_excludes_non_projects(tmp_path):
    _make_project(tmp_path, "real")
    fake = tmp_path / "fake"
    fake.mkdir()
    # No .orchid.yaml in fake

    d = ProjectDiscovery(watch_dirs=[tmp_path])
    found = d.scan()
    names = {p.name for p in found}
    assert "real" in names
    assert "fake" not in names


def test_explicit_projects_always_included(tmp_path):
    explicit = tmp_path / "explicit_proj"
    explicit.mkdir()
    # No .orchid.yaml — explicit projects bypass the check

    d = ProjectDiscovery(watch_dirs=[tmp_path], explicit_projects=[explicit])
    found = d.scan()
    assert explicit.resolve() in found


def test_scan_nonexistent_watch_dir(tmp_path):
    missing = tmp_path / "does_not_exist"
    d = ProjectDiscovery(watch_dirs=[missing])
    # Should not raise; returns empty list
    found = d.scan()
    assert found == []


def test_scan_deduplicates(tmp_path):
    proj = _make_project(tmp_path, "alpha")
    # Pass same dir twice and also as explicit
    d = ProjectDiscovery(watch_dirs=[tmp_path, tmp_path], explicit_projects=[proj])
    found = d.scan()
    assert found.count(proj.resolve()) == 1


# ── AgentManager tests ────────────────────────────────────────────────────────


def test_agent_manager_starts_auto_run_projects(tmp_path):
    proj = _make_project(tmp_path, "autorun")
    config = ProjectConfig(
        project_id="autorun",
        path=str(proj),
        auto_run=True,
    )
    run_called = threading.Event()

    mgr = AgentManager([config])

    with patch.object(mgr, "_run_project") as mock_run:
        def _fake_run(proj_config):
            run_called.set()
        mock_run.side_effect = _fake_run

        # trigger() is called by start() for auto_run projects without schedule
        mgr.start()
        run_called.wait(timeout=1.0)

    mock_run.assert_called_once()
    mgr.stop()


def test_agent_manager_respects_auto_run_false(tmp_path):
    proj = _make_project(tmp_path, "notauto")
    config = ProjectConfig(
        project_id="notauto",
        path=str(proj),
        auto_run=False,
    )
    mgr = AgentManager([config])

    with patch.object(mgr, "_run_project") as mock_run:
        mgr.start()
        time.sleep(0.1)
        mock_run.assert_not_called()

    mgr.stop()


def test_agent_manager_trigger_returns_false_if_running(tmp_path):
    proj = _make_project(tmp_path, "busy")
    config = ProjectConfig(project_id="busy", path=str(proj))
    mgr = AgentManager([config])

    # Manually mark as running
    mgr._states["busy"].running = True
    result = mgr.trigger("busy")
    assert result is False


def test_agent_manager_trigger_returns_false_unknown(tmp_path):
    mgr = AgentManager([])
    result = mgr.trigger("nonexistent")
    assert result is False


def test_schedule_parses_cron_expression(tmp_path):
    """APScheduler cron expression is parsed from 5-field string."""
    proj = _make_project(tmp_path, "scheduled")
    config = ProjectConfig(
        project_id="scheduled",
        path=str(proj),
        auto_run=True,
        auto_run_schedule=["0 2 * * *"],
    )
    mgr = AgentManager([config])

    mock_scheduler = MagicMock()

    # Patch at the source since BackgroundScheduler is lazily imported inside the method
    with patch("apscheduler.schedulers.background.BackgroundScheduler", return_value=mock_scheduler):
        mgr._start_scheduler([config])

    # Verify a job was added with the correct cron fields
    mock_scheduler.add_job.assert_called_once()
    call_kwargs = mock_scheduler.add_job.call_args
    assert call_kwargs.kwargs.get("minute") == "0"
    assert call_kwargs.kwargs.get("hour") == "2"


def test_schedule_invalid_cron_logs_warning(tmp_path, caplog):
    proj = _make_project(tmp_path, "badcron")
    config = ProjectConfig(
        project_id="badcron",
        path=str(proj),
        auto_run=True,
        auto_run_schedule=["not-a-valid-cron"],
    )
    mgr = AgentManager([config])
    mock_scheduler = MagicMock()

    import logging
    with patch("apscheduler.schedulers.background.BackgroundScheduler", return_value=mock_scheduler):
        with caplog.at_level(logging.WARNING, logger="orchid.agent_manager"):
            mgr._start_scheduler([config])

    mock_scheduler.add_job.assert_not_called()


def test_agent_manager_status_unknown():
    mgr = AgentManager([])
    result = mgr.status("nonexistent")
    assert "error" in result


def test_agent_manager_status_running(tmp_path):
    proj = _make_project(tmp_path, "check")
    config = ProjectConfig(project_id="check", path=str(proj))
    mgr = AgentManager([config])
    assert mgr.status("check")["running"] is False


# ── Web server discovery integration ─────────────────────────────────────────


def test_deletion_only_removes_specific_project(tmp_path):
    """Deleting one project unregisters only that project — others are untouched."""
    import orchid.interfaces.web_server as ws

    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()
    ws._discovery = None

    proj_a = _make_project(tmp_path, "proj_a")
    proj_b = _make_project(tmp_path, "proj_b")

    ws._register_project(str(proj_a))
    ws._register_project(str(proj_b))

    assert "proj_a" in ws._projects
    assert "proj_b" in ws._projects

    # Delete proj_a and unregister it
    import shutil
    shutil.rmtree(str(proj_a))

    result = ws._unregister_project(str(proj_a))

    assert result == "proj_a"
    assert "proj_a" not in ws._projects
    assert "proj_b" in ws._projects          # untouched
    assert ws._projects["proj_b"] == str(proj_b.resolve())


def test_web_server_registers_discovered_project(tmp_path):
    """create_app() with watch_dirs registers auto-discovered projects."""
    import orchid.interfaces.web_server as ws

    # Reset module state
    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()
    ws._discovery = None

    proj = _make_project(tmp_path, "discovered")
    (tmp_path / "tasks.md").write_text("# Tasks\n\n## TODO\n\n## DONE\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Hot memory\n", encoding="utf-8")

    app = ws.create_app(
        project_paths=[],
        watch_dirs=[str(tmp_path)],
    )

    # After create_app, the discovered project should be registered
    assert "discovered" in ws._projects
    assert ws._projects["discovered"] == str(proj.resolve())
