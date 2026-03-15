"""Tests for task dependency system."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from orchid.memory.state import (
    Task, TaskStatus, load_tasks, save_tasks, next_task,
    detect_dependency_cycles,
)


def test_task_with_no_deps_is_runnable():
    t = Task(id="T001", title="No deps")
    assert t.is_runnable(set()) is True
    assert t.is_runnable({"T002"}) is True


def test_task_blocked_by_incomplete_dep():
    t = Task(id="T002", title="Needs T001", depends_on=["T001"])
    assert t.is_runnable(set()) is False
    assert t.is_runnable({"T003"}) is False


def test_task_unblocked_when_dep_completes():
    t = Task(id="T002", title="Needs T001", depends_on=["T001"])
    assert t.is_runnable({"T001"}) is True


def test_multiple_deps_all_required():
    t = Task(id="T003", title="Needs T001 and T002", depends_on=["T001", "T002"])
    assert t.is_runnable({"T001"}) is False
    assert t.is_runnable({"T002"}) is False
    assert t.is_runnable({"T001", "T002"}) is True


def test_circular_dependency_detected():
    tasks = [
        Task(id="T001", title="A", depends_on=["T002"]),
        Task(id="T002", title="B", depends_on=["T001"]),
    ]
    cycles = detect_dependency_cycles(tasks)
    assert len(cycles) > 0
    # Each cycle should contain both T001 and T002
    cycle_ids = set(cycles[0])
    assert "T001" in cycle_ids
    assert "T002" in cycle_ids


def test_no_cycles_detected_for_valid_deps():
    tasks = [
        Task(id="T001", title="A"),
        Task(id="T002", title="B", depends_on=["T001"]),
        Task(id="T003", title="C", depends_on=["T002"]),
    ]
    cycles = detect_dependency_cycles(tasks)
    assert cycles == []


def test_deps_parsed_from_tasks_md(tmp_path):
    content = (
        "# Tasks\n\n"
        "## TODO\n\n"
        "- [ ] **T001** First task `type:draft` `p1`\n"
        "- [ ] **T002** Second task `type:draft` `p2` `needs:T001`\n"
        "- [ ] **T003** Third task `type:code_generate` `p1` `needs:T001,T002`\n"
    )
    (tmp_path / "tasks.md").write_text(content, encoding="utf-8")
    tasks = load_tasks(tmp_path)
    task_map = {t.id: t for t in tasks}

    assert task_map["T001"].depends_on == []
    assert task_map["T002"].depends_on == ["T001"]
    assert task_map["T003"].depends_on == ["T001", "T002"]


def test_topological_sort_order(tmp_path):
    """next_task() should always return a runnable task first."""
    tasks = [
        Task(id="T001", title="No deps", priority=2),
        Task(id="T002", title="Needs T001", priority=1, depends_on=["T001"]),
    ]
    save_tasks(tasks, tmp_path)
    loaded = load_tasks(tmp_path)

    # T002 has higher priority (p1) but is blocked; T001 should be picked first
    nxt = next_task(loaded)
    assert nxt is not None
    assert nxt.id == "T001"


def test_next_task_picks_unblocked_after_completion(tmp_path):
    tasks = [
        Task(id="T001", title="Done", status=TaskStatus.DONE, priority=1),
        Task(id="T002", title="Needs T001", priority=2, depends_on=["T001"]),
        Task(id="T003", title="Also needs T001", priority=3, depends_on=["T001"]),
    ]
    save_tasks(tasks, tmp_path)
    loaded = load_tasks(tmp_path)
    nxt = next_task(loaded)
    assert nxt is not None
    assert nxt.id == "T002"  # highest priority unblocked TODO


def test_deps_round_trip(tmp_path):
    """Save and reload a task with deps — deps survive round-trip."""
    tasks = [
        Task(id="T001", title="Base"),
        Task(id="T002", title="Depends on T001", depends_on=["T001"], model_override="claude"),
    ]
    save_tasks(tasks, tmp_path)
    loaded = load_tasks(tmp_path)
    task_map = {t.id: t for t in loaded}
    assert task_map["T002"].depends_on == ["T001"]
    assert task_map["T002"].model_override == "claude"
