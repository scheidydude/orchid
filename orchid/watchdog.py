import logging
import threading
import time

from orchid.session import Session
from orchid.memory.state import TaskStatus

logger = logging.getLogger(__name__)


class TaskWatchdog:
    """Periodically scans in-progress tasks and marks any that have been
    running longer than *stuck_threshold_s* as BLOCKED."""

    def __init__(self, session: Session, stuck_threshold_s: int = 1800) -> None:
        self._session = session
        self._threshold = stuck_threshold_s
        self._in_progress_since: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- public API ---------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog background thread."""
        self._thread = threading.Thread(
            target=self._run,
            name="orchid-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the watchdog to stop and wait for the thread to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- internal loop ------------------------------------------------------

    def _run(self) -> None:
        """Main loop: check every 60 seconds until stopped."""
        while not self._stop.wait(60):
            self._check()

    # -- single check pass --------------------------------------------------

    def _check(self) -> None:
        """Scan all tasks and update stale IN_PROGRESS entries."""
        for task in self._session.tasks:
            if task.status == TaskStatus.IN_PROGRESS:
                if task.id not in self._in_progress_since:
                    self._in_progress_since[task.id] = time.monotonic()
                elif (
                    time.monotonic() - self._in_progress_since[task.id]
                    > self._threshold
                ):
                    logger.warning(
                        "[watchdog] Task %s stuck >%ds — marking BLOCKED",
                        task.id,
                        self._threshold,
                    )
                    self._session.update_task_status(task.id, TaskStatus.BLOCKED)
                    self._in_progress_since.pop(task.id, None)
            else:
                self._in_progress_since.pop(task.id, None)