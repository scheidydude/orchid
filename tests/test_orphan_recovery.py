"""Tests for resume_orphaned_tasks() — Phase 2 restart persistence."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from orchid.checkpoint.restore import resume_orphaned_tasks
from orchid.checkpoint.store import CheckpointStore
from orchid.checkpoint.schema import ReActCheckpoint
from orchid.memory.state import Task, TaskStatus


def _write_tasks(project_dir: Path, tasks: list[Task]) -> None:
    from orchid.memory.state import save_tasks
    (project_dir / ".orchid").mkdir(parents=True, exist_ok=True)
    save_tasks(tasks, project_dir)


def _make_task(task_id: str, status: TaskStatus) -> Task:
    return Task(id=task_id, title=f"Task {task_id}", status=status, priority=2, type="code_generate")


def _write_react_checkpoint(project_dir: Path, task_id: str, age_hours: float = 0.0) -> None:
    """Write a ReActCheckpoint with a specific age, bypassing save_react_checkpoint's
    timestamp override."""
    import json
    import dataclasses
    store = CheckpointStore(project_dir)
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    cp = ReActCheckpoint(
        task_id=task_id,
        iteration=5,
        conversation_history=[{"role": "user", "content": "do stuff"}],
        timestamp=ts.isoformat(),
    )
    # Write directly to avoid save_react_checkpoint overwriting the timestamp
    dest = store._dir / f"react_{task_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(dataclasses.asdict(cp)))


class TestResumeOrphanedTasks:
    def test_no_tasks_file_returns_zero(self, tmp_path):
        """No tasks.md → 0 orphans (load_tasks raises, caught gracefully)."""
        count = resume_orphaned_tasks(tmp_path)
        assert count == 0

    def test_no_in_progress_tasks_returns_zero(self, tmp_path):
        tasks = [_make_task("T001", TaskStatus.TODO)]
        _write_tasks(tmp_path, tasks)
        count = resume_orphaned_tasks(tmp_path)
        assert count == 0

    def test_in_progress_with_fresh_checkpoint_kept(self, tmp_path):
        """Task with recent checkpoint stays IN_PROGRESS (resume path)."""
        (tmp_path / ".orchid").mkdir(parents=True, exist_ok=True)
        tasks = [_make_task("T001", TaskStatus.IN_PROGRESS)]
        _write_tasks(tmp_path, tasks)
        _write_react_checkpoint(tmp_path, "T001", age_hours=0.5)

        count = resume_orphaned_tasks(tmp_path)
        assert count == 1

        from orchid.memory.state import load_tasks
        updated = load_tasks(tmp_path)
        t = next(t for t in updated if t.id == "T001")
        assert t.status == TaskStatus.IN_PROGRESS

    def test_in_progress_with_stale_checkpoint_reset_to_todo(self, tmp_path):
        """Task with >24h checkpoint reset to TODO."""
        (tmp_path / ".orchid").mkdir(parents=True, exist_ok=True)
        tasks = [_make_task("T001", TaskStatus.IN_PROGRESS)]
        _write_tasks(tmp_path, tasks)
        _write_react_checkpoint(tmp_path, "T001", age_hours=25.0)

        count = resume_orphaned_tasks(tmp_path)
        assert count == 1

        from orchid.memory.state import load_tasks
        updated = load_tasks(tmp_path)
        t = next(t for t in updated if t.id == "T001")
        assert t.status == TaskStatus.TODO

    def test_in_progress_with_no_checkpoint_reset_to_todo(self, tmp_path):
        """Task with no checkpoint at all reset to TODO."""
        (tmp_path / ".orchid").mkdir(parents=True, exist_ok=True)
        tasks = [_make_task("T001", TaskStatus.IN_PROGRESS)]
        _write_tasks(tmp_path, tasks)

        count = resume_orphaned_tasks(tmp_path)
        assert count == 1

        from orchid.memory.state import load_tasks
        updated = load_tasks(tmp_path)
        t = next(t for t in updated if t.id == "T001")
        assert t.status == TaskStatus.TODO

    def test_multiple_orphans_mixed(self, tmp_path):
        """One fresh checkpoint stays, one stale resets."""
        (tmp_path / ".orchid").mkdir(parents=True, exist_ok=True)
        tasks = [
            _make_task("T001", TaskStatus.IN_PROGRESS),  # fresh checkpoint
            _make_task("T002", TaskStatus.IN_PROGRESS),  # no checkpoint
            _make_task("T003", TaskStatus.TODO),          # not orphaned
        ]
        _write_tasks(tmp_path, tasks)
        _write_react_checkpoint(tmp_path, "T001", age_hours=1.0)

        count = resume_orphaned_tasks(tmp_path)
        assert count == 2

        from orchid.memory.state import load_tasks
        updated = {t.id: t for t in load_tasks(tmp_path)}
        assert updated["T001"].status == TaskStatus.IN_PROGRESS
        assert updated["T002"].status == TaskStatus.TODO
        assert updated["T003"].status == TaskStatus.TODO
