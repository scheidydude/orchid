"""BackgroundRunner — manages non-blocking agent execution for the Telegram bot.

Uses a single background thread (ThreadPoolExecutor) so the asyncio event
loop driving the bot never blocks.  All callbacks are scheduled back onto
the event loop with loop.call_soon_threadsafe / asyncio.run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BackgroundRunner:
    """Run orchestrator tasks in a background thread without blocking the bot."""

    def __init__(self, project_path: str) -> None:
        self.project_path = project_path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orchid-bg")
        self._current_future: Future[Any] | None = None
        self._cancel_event = threading.Event()
        self._lock = threading.RLock()  # RLock so is_running() can be called while lock is held

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        with self._lock:
            return self._current_future is not None and not self._current_future.done()

    def cancel(self) -> bool:
        """Signal the running task to stop. Returns True if a task was running."""
        with self._lock:
            if self._current_future is None or self._current_future.done():
                return False
            self._cancel_event.set()
            return True

    def run_task(
        self,
        task_id: str,
        callback: Callable[[str, str | None, str | None], Any],
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        """
        Run a single task in the background.

        callback(task_id, result_text, error_text) is called on the event loop
        when the task finishes.  result_text XOR error_text will be non-None.

        Returns False if another task is already running.
        """
        with self._lock:
            if self.is_running():
                return False
            self._cancel_event.clear()
            future = self._executor.submit(self._run_task_sync, task_id, callback, loop)
            self._current_future = future
        return True

    def run_auto(
        self,
        task_callback: Callable[[str, str | None, str | None], Any],
        done_callback: Callable[[list[str], list[str]], Any],
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        """
        Run all pending tasks autonomously.

        task_callback(task_id, result, error) called after each task.
        done_callback(done_ids, failed_ids) called when the queue is empty.

        Returns False if already running.
        """
        with self._lock:
            if self.is_running():
                return False
            self._cancel_event.clear()
            future = self._executor.submit(
                self._run_auto_sync, task_callback, done_callback, loop
            )
            self._current_future = future
        return True

    # ── Internal sync workers (run inside the thread pool) ─────────────────────

    def _make_session(self):
        from orchid.session import Session
        s = Session(project_dir=self.project_path)
        s.load()
        return s

    def _run_task_sync(
        self,
        task_id: str,
        callback: Callable[[str, str | None, str | None], Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        try:
            session = self._make_session()
            task = next((t for t in session.tasks if t.id == task_id), None)
            if task is None:
                self._fire(loop, callback, task_id, None, f"Task {task_id} not found")
                return

            from orchid.orchestrator import Orchestrator
            orch = Orchestrator(session)
            result = orch._execute_task(task)
            session.save()
            result_text = str(result.get("result", "")) if result else ""
            self._fire(loop, callback, task_id, result_text, None)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Background task %s failed", task_id)
            self._fire(loop, callback, task_id, None, str(exc))

    def _run_auto_sync(
        self,
        task_callback: Callable[[str, str | None, str | None], Any],
        done_callback: Callable[[list[str], list[str]], Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        done_ids: list[str] = []
        failed_ids: list[str] = []
        try:
            session = self._make_session()
            from orchid.orchestrator import Orchestrator
            from orchid.memory.state import TaskStatus

            orch = Orchestrator(session)

            while not self._cancel_event.is_set():
                task = session.next_task()
                if task is None:
                    break
                try:
                    result = orch._execute_task(task)
                    session.save()
                    result_text = str(result.get("result", "")) if result else ""
                    done_ids.append(task.id)
                    self._fire(loop, task_callback, task.id, result_text, None)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Auto task %s failed", task.id)
                    failed_ids.append(task.id)
                    session.update_task_status(task.id, TaskStatus.BLOCKED)
                    session.save()
                    self._fire(loop, task_callback, task.id, None, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Auto run failed: %s", exc)
        finally:
            asyncio.run_coroutine_threadsafe(
                _maybe_coro(done_callback, done_ids, failed_ids), loop
            )

    @staticmethod
    def _fire(
        loop: asyncio.AbstractEventLoop,
        callback: Callable,
        *args: Any,
    ) -> None:
        asyncio.run_coroutine_threadsafe(_maybe_coro(callback, *args), loop)

    def shutdown(self) -> None:
        self.cancel()
        self._executor.shutdown(wait=False)


async def _maybe_coro(fn: Callable, *args: Any) -> None:
    """Call fn(*args); await if it returns a coroutine."""
    result = fn(*args)
    if asyncio.iscoroutine(result):
        await result
