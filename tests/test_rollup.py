"""Tests for rollup task type: parsing, TaskResultStore, and orchestrator synthesis."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.memory.state import (
    Task,
    TaskResultStore,
    TaskStatus,
    load_tasks,
    save_tasks,
)


# ── Parsing tests ─────────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    (path / "tasks.md").write_text(content, encoding="utf-8")


def test_rollup_task_parsed_from_tasks_md(tmp_path):
    _write(tmp_path, (
        "# Tasks\n\n"
        "## TODO\n\n"
        "- [ ] **T099** Final rollup `type:rollup` `p1`"
        " `rollup:T090,T091` `output:STATUS.md`\n"
    ))
    tasks = load_tasks(tmp_path)
    assert len(tasks) == 1
    assert tasks[0].type == "rollup"


def test_rollup_sources_parsed(tmp_path):
    _write(tmp_path, (
        "# Tasks\n\n## TODO\n\n"
        "- [ ] **T099** Rollup `type:rollup` `p1` `rollup:T090,T091,T092`\n"
    ))
    t = load_tasks(tmp_path)[0]
    assert t.rollup_sources == ["T090", "T091", "T092"]


def test_rollup_output_file_parsed(tmp_path):
    _write(tmp_path, (
        "# Tasks\n\n## TODO\n\n"
        "- [ ] **T099** Rollup `type:rollup` `p1` `rollup:T001` `output:REVIEW-STATUS.md`\n"
    ))
    t = load_tasks(tmp_path)[0]
    assert t.output_file == "REVIEW-STATUS.md"


def test_rollup_output_file_none_when_absent(tmp_path):
    _write(tmp_path, (
        "# Tasks\n\n## TODO\n\n"
        "- [ ] **T099** Rollup `type:rollup` `p1` `rollup:T001`\n"
    ))
    t = load_tasks(tmp_path)[0]
    assert t.output_file is None


def test_rollup_round_trip(tmp_path):
    task = Task(
        id="T099",
        title="Final rollup",
        type="rollup",
        priority=1,
        rollup_sources=["T090", "T091"],
        output_file="REVIEW-STATUS.md",
    )
    save_tasks([task], tmp_path)
    loaded = load_tasks(tmp_path)
    assert len(loaded) == 1
    t = loaded[0]
    assert t.rollup_sources == ["T090", "T091"]
    assert t.output_file == "REVIEW-STATUS.md"


def test_rollup_default_output_filename():
    """Default output filename is ROLLUP-{task_id}.md when output_file is None."""
    from orchid import config as cfg
    template = cfg.get("rollup.default_output", "ROLLUP-{task_id}.md")
    result = template.replace("{task_id}", "T099")
    assert result == "ROLLUP-T099.md"


# ── is_runnable: rollup_sources block execution ───────────────────────────────


def test_rollup_is_runnable_when_sources_done():
    task = Task(id="T099", title="Rollup", type="rollup", rollup_sources=["T001", "T002"])
    assert task.is_runnable({"T001", "T002"})


def test_rollup_not_runnable_when_sources_incomplete():
    task = Task(id="T099", title="Rollup", type="rollup", rollup_sources=["T001", "T002"])
    assert not task.is_runnable({"T001"})  # T002 not done


def test_rollup_not_runnable_when_sources_empty_set():
    task = Task(id="T099", title="Rollup", type="rollup", rollup_sources=["T001"])
    assert not task.is_runnable(set())


# ── TaskResultStore ───────────────────────────────────────────────────────────


def test_task_result_store_append_and_get(tmp_path):
    store = TaskResultStore(tmp_path)
    store.append("T001", "Scan for imports", "review", "No issues found.")
    entry = store.get("T001")
    assert entry is not None
    assert entry["task_id"] == "T001"
    assert entry["title"] == "Scan for imports"
    assert entry["type"] == "review"
    assert entry["result"] == "No issues found."
    assert "completed_at" in entry


def test_task_result_store_get_many(tmp_path):
    store = TaskResultStore(tmp_path)
    store.append("T001", "Task one", "review", "Result one")
    store.append("T002", "Task two", "review", "Result two")
    store.append("T003", "Task three", "code_generate", "Result three")

    results = store.get_many(["T001", "T003"])
    assert len(results) == 2
    ids = [r["task_id"] for r in results]
    assert ids == ["T001", "T003"]


def test_task_result_store_get_many_preserves_order(tmp_path):
    store = TaskResultStore(tmp_path)
    store.append("T002", "Two", "review", "r2")
    store.append("T001", "One", "review", "r1")

    # Request in T001, T002 order — should match that order
    results = store.get_many(["T001", "T002"])
    assert results[0]["task_id"] == "T001"
    assert results[1]["task_id"] == "T002"


def test_task_result_store_missing_returns_none(tmp_path):
    store = TaskResultStore(tmp_path)
    assert store.get("T999") is None


def test_task_result_store_missing_get_many_skips(tmp_path):
    store = TaskResultStore(tmp_path)
    store.append("T001", "Task one", "review", "Result one")
    results = store.get_many(["T001", "T999"])  # T999 not stored
    assert len(results) == 1
    assert results[0]["task_id"] == "T001"


def test_task_result_store_get_all(tmp_path):
    store = TaskResultStore(tmp_path)
    store.append("T001", "One", "review", "r1")
    store.append("T002", "Two", "code_generate", "r2")
    all_results = store.get_all()
    assert len(all_results) == 2


def test_task_result_store_latest_wins(tmp_path):
    """When the same task_id is appended twice, get() returns the most recent."""
    store = TaskResultStore(tmp_path)
    store.append("T001", "Task", "review", "First result")
    store.append("T001", "Task", "review", "Updated result")
    entry = store.get("T001")
    assert entry["result"] == "Updated result"


def test_task_result_store_empty_project(tmp_path):
    store = TaskResultStore(tmp_path)
    assert store.get_all() == []
    assert store.get("T001") is None
    assert store.get_many(["T001"]) == []


# ── Orchestrator rollup execution ─────────────────────────────────────────────


def _make_session(tmp_path: Path, tasks: list[Task]) -> MagicMock:
    """Build a minimal mock session with the given tasks."""
    session = MagicMock()
    session.tasks = tasks
    session.project_dir = tmp_path
    session.project_name = "test-project"
    session.project_description = ""
    session.hot_memory = ""
    session.delegations = []

    def _update_status(task_id, status):
        for t in tasks:
            if t.id == task_id:
                t.status = status
                return True
        return False

    session.update_task_status.side_effect = _update_status
    return session


def test_rollup_blocked_when_sources_incomplete(tmp_path):
    from orchid.orchestrator import Orchestrator

    source = Task(id="T001", title="Source", type="review", status=TaskStatus.TODO)
    rollup = Task(
        id="T099",
        title="Rollup",
        type="rollup",
        status=TaskStatus.IN_PROGRESS,
        rollup_sources=["T001"],
    )
    session = _make_session(tmp_path, [source, rollup])

    orch = Orchestrator.__new__(Orchestrator)
    orch.session = session
    orch.cli_model_override = None
    orch.cli_provider_overrides = {}
    orch.offline_mode = False
    orch.stream_callback = None

    result = orch._execute_rollup_task(rollup)

    assert result["status"] == "blocked"
    assert "T001" in result["error"]
    assert rollup.status == TaskStatus.BLOCKED


def test_rollup_executes_when_sources_complete(tmp_path):
    from orchid.orchestrator import Orchestrator

    # Pre-populate task result store
    store = TaskResultStore(tmp_path)
    store.append("T001", "Source task", "review", "All imports OK")

    source = Task(id="T001", title="Source task", type="review", status=TaskStatus.DONE)
    rollup = Task(
        id="T099",
        title="Final rollup",
        type="rollup",
        status=TaskStatus.IN_PROGRESS,
        rollup_sources=["T001"],
    )
    session = _make_session(tmp_path, [source, rollup])

    orch = Orchestrator.__new__(Orchestrator)
    orch.session = session
    orch.cli_model_override = None
    orch.cli_provider_overrides = {}
    orch.offline_mode = False
    orch.stream_callback = None

    synthesis_text = "## Summary\n\nAll tasks passed. No issues found."

    with patch("orchid.orchestrator.call", return_value=synthesis_text):
        result = orch._execute_rollup_task(rollup)

    assert result["status"] == "done"
    assert rollup.status == TaskStatus.DONE


def test_rollup_writes_output_file(tmp_path):
    from orchid.orchestrator import Orchestrator

    store = TaskResultStore(tmp_path)
    store.append("T001", "Source", "review", "OK")

    source = Task(id="T001", title="Source", type="review", status=TaskStatus.DONE)
    rollup = Task(
        id="T099",
        title="Final rollup",
        type="rollup",
        status=TaskStatus.IN_PROGRESS,
        rollup_sources=["T001"],
        output_file="REVIEW-STATUS.md",
    )
    session = _make_session(tmp_path, [source, rollup])

    orch = Orchestrator.__new__(Orchestrator)
    orch.session = session
    orch.cli_model_override = None
    orch.cli_provider_overrides = {}
    orch.offline_mode = False
    orch.stream_callback = None

    synthesis_text = "## Status\n\nPassed."

    with patch("orchid.orchestrator.call", return_value=synthesis_text):
        result = orch._execute_rollup_task(rollup)

    output_path = tmp_path / "REVIEW-STATUS.md"
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == synthesis_text
    assert result["output_file"] == "REVIEW-STATUS.md"


def test_rollup_default_output_file_used_when_none(tmp_path):
    from orchid.orchestrator import Orchestrator

    store = TaskResultStore(tmp_path)
    store.append("T001", "Source", "review", "OK")

    source = Task(id="T001", title="Source", type="review", status=TaskStatus.DONE)
    rollup = Task(
        id="T099",
        title="Rollup",
        type="rollup",
        status=TaskStatus.IN_PROGRESS,
        rollup_sources=["T001"],
        output_file=None,  # no explicit output file
    )
    session = _make_session(tmp_path, [source, rollup])

    orch = Orchestrator.__new__(Orchestrator)
    orch.session = session
    orch.cli_model_override = None
    orch.cli_provider_overrides = {}
    orch.offline_mode = False
    orch.stream_callback = None

    with patch("orchid.orchestrator.call", return_value="Summary content"):
        result = orch._execute_rollup_task(rollup)

    assert result["output_file"] == "ROLLUP-T099.md"
    assert (tmp_path / "ROLLUP-T099.md").exists()


def test_rollup_always_uses_claude(tmp_path):
    """Rollup synthesis should always call with model_key='claude'."""
    from orchid.orchestrator import Orchestrator

    store = TaskResultStore(tmp_path)
    store.append("T001", "Source", "review", "OK")

    source = Task(id="T001", title="Source", type="review", status=TaskStatus.DONE)
    rollup = Task(
        id="T099",
        title="Rollup",
        type="rollup",
        status=TaskStatus.IN_PROGRESS,
        rollup_sources=["T001"],
    )
    session = _make_session(tmp_path, [source, rollup])

    orch = Orchestrator.__new__(Orchestrator)
    orch.session = session
    orch.cli_model_override = None
    orch.cli_provider_overrides = {}
    orch.offline_mode = False
    orch.stream_callback = None

    captured_calls = []

    def _fake_call(messages, model_key, system=""):
        captured_calls.append(model_key)
        return "Synthesis result"

    with patch("orchid.orchestrator.call", side_effect=_fake_call):
        orch._execute_rollup_task(rollup)

    assert captured_calls == ["claude"]


def test_rollup_no_sources_marks_blocked(tmp_path):
    from orchid.orchestrator import Orchestrator

    rollup = Task(
        id="T099",
        title="Empty rollup",
        type="rollup",
        status=TaskStatus.IN_PROGRESS,
        rollup_sources=[],
    )
    session = _make_session(tmp_path, [rollup])

    orch = Orchestrator.__new__(Orchestrator)
    orch.session = session
    orch.cli_model_override = None
    orch.cli_provider_overrides = {}
    orch.offline_mode = False
    orch.stream_callback = None

    result = orch._execute_rollup_task(rollup)
    assert result["status"] == "failed"
    assert rollup.status == TaskStatus.BLOCKED


# ── CLI --get-result flag ─────────────────────────────────────────────────────


def test_get_result_cli_returns_entry(tmp_path):
    """_cmd_get_result should print stored result without raising."""
    from orchid.interfaces.cli import _cmd_get_result

    store = TaskResultStore(tmp_path)
    store.append("T001", "My task", "review", "Looks good!")

    # Should not raise
    _cmd_get_result(str(tmp_path), "T001")


def test_get_result_cli_exits_on_missing(tmp_path):
    """_cmd_get_result should raise an exit exception for unknown task IDs."""
    import click
    from orchid.interfaces.cli import _cmd_get_result

    with pytest.raises((SystemExit, click.exceptions.Exit)):
        _cmd_get_result(str(tmp_path), "T999")
