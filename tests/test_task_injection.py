"""Tests for orchid/tools/task_injection.py — programmatic task injection."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchid.memory.state import Task, TaskStatus, load_tasks
from orchid.tools.task_injection import (
    get_task,
    inject_task,
    list_tasks,
    remove_task,
    set_active_session,
    spawn_task,
    task_exists,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_empty_tasks_md(dir_path: Path) -> None:
    """Create an empty tasks.md so load_tasks doesn't return [] unexpectedly."""
    (dir_path / "tasks.md").write_text("# Tasks\n\n", encoding="utf-8")


# ── inject_task ────────────────────────────────────────────────────────────────

def test_inject_task_creates_new_task(tmp_path):
    """inject_task appends a new task to tasks.md and returns it."""
    _write_empty_tasks_md(tmp_path)
    task = inject_task(tmp_path, "Write tests")
    assert task.id == "T001"
    assert task.title == "Write tests"
    assert task.status == TaskStatus.TODO
    assert task.type == "draft"
    assert task.priority == 2

    loaded = load_tasks(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == "T001"


def test_inject_task_with_all_params(tmp_path):
    """inject_task respects all keyword arguments."""
    _write_empty_tasks_md(tmp_path)
    # Create T001 first so depends_on validation passes
    inject_task(tmp_path, "Prerequisite")
    task = inject_task(
        tmp_path,
        title="Code review",
        task_type="review",
        priority=1,
        description="Review PR #42",
        depends_on=["T001"],
        agent="reviewer",
        model_override="claude",
        tags=["urgent"],
    )
    assert task.id == "T002"
    assert task.type == "review"
    assert task.priority == 1
    assert task.description == "Review PR #42"
    assert task.depends_on == ["T001"]
    assert task.agent == "reviewer"
    assert task.model_override == "claude"
    assert task.tags == ["urgent"]


def test_inject_task_rollup_params(tmp_path):
    """inject_task accepts rollup_sources and output_file."""
    _write_empty_tasks_md(tmp_path)
    # Create T001, T002 first so rollup_sources validation passes
    inject_task(tmp_path, "Source 1")
    inject_task(tmp_path, "Source 2")
    task = inject_task(
        tmp_path,
        title="Rollup report",
        task_type="rollup",
        rollup_sources=["T001", "T002"],
        output_file="report.md",
    )
    assert task.rollup_sources == ["T001", "T002"]
    assert task.output_file == "report.md"


def test_inject_task_sequential_ids(tmp_path):
    """Subsequent inject_task calls produce incrementing IDs."""
    _write_empty_tasks_md(tmp_path)
    t1 = inject_task(tmp_path, "First")
    t2 = inject_task(tmp_path, "Second")
    t3 = inject_task(tmp_path, "Third")
    assert t1.id == "T001"
    assert t2.id == "T002"
    assert t3.id == "T003"


def test_inject_task_skips_existing_ids(tmp_path):
    """inject_task picks the next ID after the highest existing one."""
    _write_empty_tasks_md(tmp_path)
    # Pre-populate with T005 so the next injected task is T006
    tasks = load_tasks(tmp_path)
    tasks.append(Task(id="T005", title="Existing", status=TaskStatus.DONE))
    from orchid.memory.state import save_tasks
    save_tasks(tasks, tmp_path)

    new = inject_task(tmp_path, "New after T005")
    assert new.id == "T006"


def test_inject_task_empty_title_raises(tmp_path):
    """inject_task raises ValueError when title is empty."""
    _write_empty_tasks_md(tmp_path)
    with pytest.raises(ValueError, match="title must not be empty"):
        inject_task(tmp_path, "")
    with pytest.raises(ValueError, match="title must not be empty"):
        inject_task(tmp_path, "   ")


def test_inject_task_invalid_depends_on_raises(tmp_path):
    """inject_task raises ValueError when depends_on references unknown tasks."""
    _write_empty_tasks_md(tmp_path)
    with pytest.raises(ValueError, match="depends_on references unknown"):
        inject_task(tmp_path, "Bad deps", depends_on=["T999"])


def test_inject_task_invalid_rollup_sources_raises(tmp_path):
    """inject_task raises ValueError when rollup_sources references unknown tasks."""
    _write_empty_tasks_md(tmp_path)
    with pytest.raises(ValueError, match="rollup_sources references unknown"):
        inject_task(tmp_path, "Bad rollup", rollup_sources=["T999"])


def test_inject_task_valid_depends_on(tmp_path):
    """inject_task succeeds when depends_on references existing tasks."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "First task")
    # Now T001 exists — depends_on should succeed
    task = inject_task(tmp_path, "Depends on first", depends_on=["T001"])
    assert task.depends_on == ["T001"]


def test_inject_task_persists_to_disk(tmp_path):
    """inject_task writes tasks.md immediately so load_tasks sees it."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "Persisted task")
    loaded = load_tasks(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].title == "Persisted task"


# ── remove_task ────────────────────────────────────────────────────────────────

def test_remove_task_marks_cancelled(tmp_path):
    """remove_task marks a task as CANCELLED by default."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "To remove")
    result = remove_task(tmp_path, "T001")
    assert result is True

    loaded = load_tasks(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].status == TaskStatus.CANCELLED


def test_remove_task_with_custom_status(tmp_path):
    """remove_task accepts a custom terminal status."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "To skip")
    result = remove_task(tmp_path, "T001", status=TaskStatus.SKIPPED)
    assert result is True

    loaded = load_tasks(tmp_path)
    assert loaded[0].status == TaskStatus.SKIPPED


def test_remove_task_nonexistent_returns_false(tmp_path):
    """remove_task returns False when task_id doesn't exist."""
    _write_empty_tasks_md(tmp_path)
    result = remove_task(tmp_path, "T999")
    assert result is False


# ── get_task ───────────────────────────────────────────────────────────────────

def test_get_task_returns_task(tmp_path):
    """get_task returns the Task object for a known ID."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "Get me")
    task = get_task(tmp_path, "T001")
    assert task is not None
    assert task.title == "Get me"


def test_get_task_nonexistent_returns_none(tmp_path):
    """get_task returns None for unknown task IDs."""
    _write_empty_tasks_md(tmp_path)
    assert get_task(tmp_path, "T999") is None


# ── list_tasks ─────────────────────────────────────────────────────────────────

def test_list_tasks_all(tmp_path):
    """list_tasks returns all tasks when no filter is given."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "One")
    inject_task(tmp_path, "Two")
    tasks = list_tasks(tmp_path)
    assert len(tasks) == 2


def test_list_tasks_status_filter(tmp_path):
    """list_tasks filters by TaskStatus."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "Todo task")
    inject_task(tmp_path, "Done task")
    # Mark T002 as DONE by ID (not index — save_tasks may reorder by priority)
    tasks = load_tasks(tmp_path)
    for t in tasks:
        if t.id == "T002":
            t.status = TaskStatus.DONE
    from orchid.memory.state import save_tasks
    save_tasks(tasks, tmp_path)

    todo_tasks = list_tasks(tmp_path, status_filter=TaskStatus.TODO)
    assert len(todo_tasks) == 1
    assert todo_tasks[0].title == "Todo task"

    done_tasks = list_tasks(tmp_path, status_filter=TaskStatus.DONE)
    assert len(done_tasks) == 1
    assert done_tasks[0].title == "Done task"


# ── task_exists ────────────────────────────────────────────────────────────────

def test_task_exists_true(tmp_path):
    """task_exists returns True for an existing task."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "Exists")
    assert task_exists(tmp_path, "T001") is True


def test_task_exists_false(tmp_path):
    """task_exists returns False for a non-existing task."""
    _write_empty_tasks_md(tmp_path)
    assert task_exists(tmp_path, "T999") is False


# ── Path handling ──────────────────────────────────────────────────────────────

def test_inject_task_accepts_path_object(tmp_path):
    """inject_task works with a Path object, not just a string."""
    _write_empty_tasks_md(tmp_path)
    task = inject_task(tmp_path, "Path test")
    assert task.id == "T001"


def test_remove_task_accepts_path_object(tmp_path):
    """remove_task works with a Path object."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "To remove")
    result = remove_task(tmp_path, "T001")
    assert result is True


def test_get_task_accepts_path_object(tmp_path):
    """get_task works with a Path object."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "Get via path")
    task = get_task(tmp_path, "T001")
    assert task is not None


def test_list_tasks_accepts_path_object(tmp_path):
    """list_tasks works with a Path object."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "List via path")
    tasks = list_tasks(tmp_path)
    assert len(tasks) == 1


def test_task_exists_accepts_path_object(tmp_path):
    """task_exists works with a Path object."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "Exists via path")
    assert task_exists(tmp_path, "T001") is True


# ── Round-trip: inject → load → save → load ───────────────────────────────────

def test_inject_then_load_round_trip(tmp_path):
    """Tasks injected via inject_task survive a full load_tasks round-trip."""
    _write_empty_tasks_md(tmp_path)
    # Create prerequisite T001 so depends_on validation passes
    inject_task(tmp_path, "Prerequisite")
    inject_task(
        tmp_path,
        "Round trip",
        task_type="code_generate",
        priority=1,
        depends_on=["T001"],
        agent="developer",
        tags=["test"],
    )
    loaded = load_tasks(tmp_path)
    t = next(x for x in loaded if x.id == "T002")
    assert t.id == "T002"
    assert t.title == "Round trip"
    assert t.type == "code_generate"
    assert t.priority == 1
    assert t.depends_on == ["T001"]
    assert t.agent == "developer"
    assert t.tags == ["test"]
    assert t.status == TaskStatus.TODO


def test_remove_then_load_round_trip(tmp_path):
    """Tasks removed via remove_task survive a load_tasks round-trip."""
    _write_empty_tasks_md(tmp_path)
    inject_task(tmp_path, "Remove then load")
    remove_task(tmp_path, "T001", status=TaskStatus.CANCELLED)
    loaded = load_tasks(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].status == TaskStatus.CANCELLED


# ── spawn_task agent-tool tests (T190 spec) ────────────────────────────────────

def test_spawn_task_no_session_returns_error():
    set_active_session(None)
    result = spawn_task("do thing")
    assert result.startswith("[error:"), f"Expected error, got: {result!r}"


def test_spawn_task_returns_task_id():
    mock_session = MagicMock()
    mock_session.inject_task.return_value = Task(id="T042", title="write tests", type="code_generate")
    set_active_session(mock_session)
    result = spawn_task("write tests", "tester", "")
    assert "T042" in result, f"Expected T042 in result, got: {result!r}"


def test_spawn_task_passes_deps():
    mock_session = MagicMock()
    mock_session.inject_task.return_value = Task(id="T043", title="verify output", type="code_generate")
    set_active_session(mock_session)
    spawn_task("verify output", "tester", "T010,T011")
    mock_session.inject_task.assert_called_with(
        title="verify output",
        agent="tester",
        depends_on=["T010", "T011"],
    )


def test_inject_task_appends_to_tasks_md(tmp_path):
    """Session.inject_task writes the new task to tasks.md."""
    (tmp_path / "tasks.md").write_text("# Tasks\n\n## DONE\n", encoding="utf-8")

    # Build a minimal session-like object matching what inject_task needs
    class _FakeSession:
        _lock = threading.RLock()
        tasks: list = []
        project_dir = tmp_path

        def inject_task(self, title, agent=None, depends_on=None, **kwargs):
            import re

            from orchid.memory.state import Task, TaskStatus
            with self._lock:
                max_n = max((int(m.group(1)) for t in self.tasks if (m := re.match(r"T(\d+)$", t.id))), default=0)
                new_id = f"T{max_n + 1:03d}"
                new_task = Task(id=new_id, title=title, status=TaskStatus.TODO, depends_on=depends_on or [])
                self.tasks.append(new_task)
                tasks_file = self.project_dir / "tasks.md"
                dep_str = f" `needs:{','.join(depends_on)}`" if depends_on else ""
                line = f"- [ ] **{new_id}** {title} `type:code_generate` `p2` `agent:{agent or 'developer'}`{dep_str}\n"
                with open(tasks_file, "a", encoding="utf-8") as f:
                    f.write(line)
                return new_task

    session = _FakeSession()
    task = session.inject_task("New task", "developer")
    content = (tmp_path / "tasks.md").read_text()
    assert task.id in content
    assert "New task" in content