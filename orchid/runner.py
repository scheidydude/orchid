"""orchid/runner.py — Web-UI background runner.

Manages per-project auto-runs in background threads.
Stop is cooperative: the cancel event is checked between tasks,
so the current task finishes before the run stops.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _ProjectState:
    future: Future[Any] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    current_task: str | None = None
    tasks_done: int = 0


class BackgroundRunner:
    """Single instance that manages auto-runs for multiple projects."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="orchid-web")
        self._states: dict[str, _ProjectState] = {}
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────────────────────

    def start(self, project_path: str) -> bool:
        """Start an auto-run for *project_path*. Returns False if already running."""
        with self._lock:
            state = self._states.get(project_path)
            if state and state.future and not state.future.done():
                return False
            state = _ProjectState()
            self._states[project_path] = state
            state.future = self._executor.submit(self._run, project_path, state)
        return True

    def stop(self, project_path: str) -> bool:
        """Signal the run to stop after the current task. Returns False if not running."""
        with self._lock:
            state = self._states.get(project_path)
            if not state or not state.future or state.future.done():
                return False
            state.cancel_event.set()
        return True

    def get_status(self, project_path: str) -> dict[str, Any]:
        with self._lock:
            state = self._states.get(project_path)
        if not state or not state.future or state.future.done():
            return {"running": False, "current_task": None, "tasks_done": 0}
        return {
            "running": True,
            "current_task": state.current_task,
            "tasks_done": state.tasks_done,
        }

    # ── Internal ────────────────────────────────────────────────────────────────

    def _run(self, project_path: str, state: _ProjectState) -> None:
        try:
            from orchid.memory.state import TaskStatus
            from orchid.orchestrator import Orchestrator
            from orchid.session import Session

            session = Session(project_dir=project_path)
            session.load()
            orch = Orchestrator(session)

            while not state.cancel_event.is_set():
                task = session.next_task()
                if task is None:
                    break

                state.current_task = f"{task.id}: {task.title}"
                try:
                    orch._execute_task(task)
                    session.save()
                    state.tasks_done += 1
                except Exception:
                    logger.exception("Task %s failed", task.id)
                    session.update_task_status(task.id, TaskStatus.BLOCKED)
                    session.save()
        except Exception:
            logger.exception("Auto-run failed for %s", project_path)
        finally:
            state.current_task = None
