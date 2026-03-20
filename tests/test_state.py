"""Tests for memory/state: task parsing and round-trip serialization."""

from __future__ import annotations

import tempfile
from pathlib import Path

from orchid.memory.state import (
    Task, TaskStatus, load_tasks, save_tasks, next_task,
    load_hot_memory, save_hot_memory, TaskResultStore,
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


def test_task_result_store_skips_corrupt_lines(tmp_path):
    """_read_all() should skip malformed JSON lines without crashing."""
    store = TaskResultStore(tmp_path)
    store._path.parent.mkdir(parents=True, exist_ok=True)

    import json
    good = json.dumps({"task_id": "T001", "title": "A", "type": "draft",
                       "completed_at": "2026-01-01T00:00:00+00:00", "result": "ok"})
    store._path.write_text(
        good + "\n"
        "THIS IS NOT JSON\n"
        + good.replace("T001", "T002") + "\n",
        encoding="utf-8",
    )

    results = store._read_all()
    assert len(results) == 2
    assert results[0]["task_id"] == "T001"
    assert results[1]["task_id"] == "T002"


def test_task_to_md_line():
    t = Task(id="T001", title="Do something", type="code_generate", priority=1, tags=["python"])
    line = t.to_md_line()
    assert "T001" in line
    assert "code_generate" in line
    assert "#python" in line
