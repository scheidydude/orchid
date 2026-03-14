"""Tests for memory/state: task parsing and round-trip serialization."""

from __future__ import annotations

import tempfile
from pathlib import Path

from orchid.memory.state import (
    Task, TaskStatus, load_tasks, save_tasks, next_task,
    load_hot_memory, save_hot_memory,
)


def _write_tasks_md(dir_path: Path, content: str) -> None:
    (dir_path / "tasks.md").write_text(content, encoding="utf-8")


def test_load_empty_tasks(tmp_path):
    tasks = load_tasks(tmp_path)
    assert tasks == []


def test_task_round_trip(tmp_path):
    tasks = [
        Task(id="T001", title="Write tests", status=TaskStatus.TODO, type="code_generate", priority=1),
        Task(id="T002", title="Review code", status=TaskStatus.IN_PROGRESS, type="review", priority=2),
        Task(id="T003", title="Deploy", status=TaskStatus.DONE, type="draft", priority=3),
    ]
    save_tasks(tasks, tmp_path)
    loaded = load_tasks(tmp_path)
    assert len(loaded) == 3
    ids = {t.id for t in loaded}
    assert ids == {"T001", "T002", "T003"}


def test_next_task_priority(tmp_path):
    tasks = [
        Task(id="T001", title="Low", status=TaskStatus.TODO, priority=3),
        Task(id="T002", title="High", status=TaskStatus.TODO, priority=1),
        Task(id="T003", title="Done", status=TaskStatus.DONE, priority=1),
    ]
    save_tasks(tasks, tmp_path)
    loaded = load_tasks(tmp_path)
    nxt = next_task(loaded)
    assert nxt is not None
    assert nxt.id == "T002"


def test_hot_memory_round_trip(tmp_path):
    content = "# CLAUDE.md\n\nSome hot memory content."
    save_hot_memory(content, tmp_path)
    loaded = load_hot_memory(tmp_path)
    assert loaded == content


def test_task_to_md_line():
    t = Task(id="T001", title="Do something", type="code_generate", priority=1, tags=["python"])
    line = t.to_md_line()
    assert "T001" in line
    assert "code_generate" in line
    assert "#python" in line
