import time
from pathlib import Path
from unittest.mock import MagicMock

from orchid.watchdog import TaskWatchdog
from orchid.memory.state import Task, TaskStatus


def _make_task(task_id: str, status: TaskStatus) -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        status=status,
        type="code_generate",
        priority=2,
        description="",
        depends_on=[],
        tags=[],
    )


def test_watchdog_starts_and_stops(tmp_path: Path):
    """Create a mock Session with tasks=[], start() then stop(), assert no exception."""
    session = MagicMock()
    session.tasks = []

    watchdog = TaskWatchdog(session, stuck_threshold_s=60)
    watchdog.start()
    watchdog.stop()
    # If we reach here without raising, the test passes


def test_watchdog_marks_stuck_task_blocked(tmp_path: Path):
    """A task stuck IN_PROGRESS longer than the threshold gets marked BLOCKED."""
    session = MagicMock()
    session.tasks = [_make_task("T001", TaskStatus.IN_PROGRESS)]

    watchdog = TaskWatchdog(session, stuck_threshold_s=0)

    # First call: records start time
    watchdog._check()

    # Second call: with threshold=0, any time elapsed is over threshold
    time.sleep(0.01)
    watchdog._check()

    session.update_task_status.assert_called_once_with("T001", TaskStatus.BLOCKED)


def test_watchdog_does_not_mark_completed_task(tmp_path: Path):
    """A task with status DONE is never marked BLOCKED."""
    session = MagicMock()
    session.tasks = [_make_task("T001", TaskStatus.DONE)]

    watchdog = TaskWatchdog(session, stuck_threshold_s=0)

    watchdog._check()

    session.update_task_status.assert_not_called()


def test_watchdog_clears_in_progress_dict_on_completion(tmp_path: Path):
    """When a task transitions from IN_PROGRESS to DONE, it is removed from _in_progress_since."""
    session = MagicMock()
    task = _make_task("T001", TaskStatus.IN_PROGRESS)
    session.tasks = [task]

    watchdog = TaskWatchdog(session, stuck_threshold_s=60)

    # First call: records T001 as in_progress
    watchdog._check()
    assert "T001" in watchdog._in_progress_since

    # Change task status to DONE
    task.status = TaskStatus.DONE

    # Second call: T001 is now DONE, should be removed from _in_progress_since
    watchdog._check()

    assert "T001" not in watchdog._in_progress_since