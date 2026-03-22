"""MultiOrchid — process-per-project parallelism coordinator.

Architecture:
  D0021: Process-per-project isolation — each project gets an independent
         worker process with its own Session, Orchestrator, agents, file handles.
  D0022: Claude API rate limiting via multiprocessing.Semaphore — workers
         monkey-patch orchid.tools.models.call to gate Claude calls.
  D0023: Notification routing via multiprocessing.Queue — workers push events;
         coordinator drains the queue and forwards to notification_callback.
"""

from __future__ import annotations

import logging
import multiprocessing
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Worker (top-level for pickling) ───────────────────────────────────────────


def worker_main(
    project_path: str,
    notification_queue: multiprocessing.Queue[dict[str, Any]],
    api_semaphore: multiprocessing.Semaphore,
    local_semaphore: multiprocessing.Semaphore,
    stop_event: multiprocessing.Event,
    code_model: str | None,
) -> None:
    """Worker process entry point.

    Runs the orchestrator for one project, draining its task queue until:
    - No more runnable tasks remain, OR
    - stop_event is set (graceful shutdown), OR
    - An unrecoverable exception escapes.

    Lifecycle events are pushed to notification_queue as dicts:
      {"event": str, "project": str, "data": dict}

    Claude API calls are rate-limited via api_semaphore.
    Local LLM calls are rate-limited via local_semaphore.
    """
    import logging as _log_mod

    _log_mod.basicConfig(
        level=_log_mod.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = _log_mod.getLogger(f"orchid.worker.{Path(project_path).name}")
    project_name = Path(project_path).name

    def _notify(event: str, data: dict[str, Any]) -> None:
        try:
            notification_queue.put_nowait({"event": event, "project": project_name, "data": data})
        except Exception as exc:
            log.warning("Notification enqueue failed (%s): %s", event, exc)

    try:
        _install_semaphore_wrapper(api_semaphore, local_semaphore)

        from orchid.memory.state import TaskStatus
        from orchid.orchestrator import Orchestrator
        from orchid.session import Session

        session = Session(project_dir=project_path)
        session.load()

        pending_count = sum(1 for t in session.tasks if t.status == TaskStatus.TODO)
        _notify("session_start", {"pending": pending_count})

        orch = Orchestrator(session, cli_model_override=code_model)

        def _stream_cb(data: dict[str, Any]) -> None:
            if data.get("event") == "task_progress":
                _notify("task_progress", data)

        orch.stream_callback = _stream_cb

        while not stop_event.is_set():
            task = session.next_task()
            if task is None:
                break

            remaining = sum(1 for t in session.tasks if t.status == TaskStatus.TODO)
            _notify("task_start", {
                "task_id": task.id,
                "title": task.title,
                "remaining": remaining,
            })

            try:
                result = orch._execute_task(task)
                session.save()
                result_text = str(result.get("result", "")) if result else ""
                _notify("task_complete", {
                    "task_id": task.id,
                    "result_snippet": result_text[:200],
                })
            except Exception as exc:
                log.exception("Task %s failed: %s", task.id, exc)
                session.update_task_status(task.id, TaskStatus.BLOCKED)
                session.save()
                _notify("task_failed", {"task_id": task.id, "error": str(exc)})

        tasks_done = sum(1 for t in session.tasks if t.status.value == "DONE")
        session.close(summary="Multi-project worker run complete.")
        _notify("session_complete", {"tasks_done": tasks_done})
        log.info("Worker for %s done. tasks_done=%d", project_name, tasks_done)

    except Exception as exc:
        log.exception("Worker for %s crashed: %s", project_path, exc)
        _notify("worker_crash", {"error": str(exc)})
        raise


def _install_semaphore_wrapper(
    api_semaphore: multiprocessing.Semaphore,
    local_semaphore: multiprocessing.Semaphore,
) -> None:
    """Monkey-patch orchid.tools.models.call in the worker process.

    Wraps Claude API calls with api_semaphore and local LLM calls with
    local_semaphore to prevent OOM on the inference server under multi-project load.
    """
    import orchid.tools.models as _models

    _original = _models.call

    def _wrapped(messages: Any, model_key: str = "local", system: str | None = None) -> str:
        sem = api_semaphore if model_key == "claude" else local_semaphore
        sem.acquire()
        try:
            return _original(messages, model_key=model_key, system=system)
        finally:
            sem.release()

    _models.call = _wrapped


# ── MultiOrchid coordinator ────────────────────────────────────────────────────


class MultiOrchid:
    """Coordinate parallel execution across multiple independent projects.

    One worker process per project.  The coordinator loop:
    - Drains notification_queue and forwards events to notification_callback.
    - Monitors worker health and restarts crashed workers up to max_restarts.
    - Responds to SIGINT/SIGTERM by stopping all workers gracefully.
    """

    def __init__(
        self,
        projects: list[Path | str],
        code_model: str | None = None,
        notification_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        from orchid import config as cfg

        self.projects = [Path(p).resolve() for p in projects]
        self.code_model = code_model
        self.notification_callback = notification_callback

        max_claude = cfg.get("multi.max_concurrent_claude_calls", 3)
        max_local = cfg.get("multi.max_concurrent_local_calls", 1)
        self._api_semaphore: multiprocessing.Semaphore = multiprocessing.Semaphore(max_claude)
        self._local_semaphore: multiprocessing.Semaphore = multiprocessing.Semaphore(max_local)
        self._notification_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._stop_event: multiprocessing.Event = multiprocessing.Event()

        self._workers: dict[str, multiprocessing.Process] = {}
        self._restart_counts: dict[str, int] = {}
        self._max_restarts: int = cfg.get("multi.max_restarts", 3)
        self._restart_on_crash: bool = cfg.get("multi.restart_on_crash", True)
        self._health_interval: float = cfg.get("multi.worker_health_check_interval", 30)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn all workers and run the coordinator loop (blocks until all done)."""
        logger.info("MultiOrchid: starting %d project(s)", len(self.projects))
        for project in self.projects:
            self._spawn_worker(str(project))

        _orig_sigint = signal.getsignal(signal.SIGINT)
        _orig_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        try:
            self._coordinator_loop()
        finally:
            signal.signal(signal.SIGINT, _orig_sigint)
            signal.signal(signal.SIGTERM, _orig_sigterm)

    def stop(self) -> None:
        """Signal all workers to stop and wait for them to exit."""
        logger.info("MultiOrchid: stopping…")
        self._stop_event.set()
        self._terminate_workers(timeout=30)

    def status(self) -> dict[str, dict[str, Any]]:
        """Return process-level status for every managed project."""
        result: dict[str, dict[str, Any]] = {}
        for project_path, proc in self._workers.items():
            name = Path(project_path).name
            result[name] = {
                "alive": proc.is_alive(),
                "pid": proc.pid,
                "exitcode": proc.exitcode,
                "restarts": self._restart_counts.get(project_path, 0),
            }
        return result

    # ── Internal ───────────────────────────────────────────────────────────────

    def _spawn_worker(self, project_path: str) -> None:
        proc = multiprocessing.Process(
            target=worker_main,
            args=(
                project_path,
                self._notification_queue,
                self._api_semaphore,
                self._local_semaphore,
                self._stop_event,
                self.code_model,
            ),
            name=f"orchid-{Path(project_path).name}",
            daemon=True,
        )
        proc.start()
        self._workers[project_path] = proc
        logger.info("Spawned worker PID=%d for %s", proc.pid, project_path)

    def _coordinator_loop(self) -> None:
        last_health = time.monotonic()

        while not self._stop_event.is_set():
            self._drain_notifications()

            if time.monotonic() - last_health >= self._health_interval:
                self._check_worker_health()
                last_health = time.monotonic()

            if all(not proc.is_alive() for proc in self._workers.values()):
                logger.info("MultiOrchid: all workers finished.")
                break

            time.sleep(0.5)

        self._drain_notifications()  # final drain after workers exit

    def _drain_notifications(self) -> None:
        while True:
            try:
                notification = self._notification_queue.get_nowait()
            except Exception:
                break
            if self.notification_callback:
                try:
                    self.notification_callback(notification)
                except Exception as exc:
                    logger.warning("notification_callback raised: %s", exc)

    def _check_worker_health(self) -> None:
        if not self._restart_on_crash:
            return

        for project_path, proc in list(self._workers.items()):
            if proc.is_alive() or proc.exitcode in (0, None):
                continue

            project_name = Path(project_path).name
            restart_count = self._restart_counts.get(project_path, 0)

            if restart_count < self._max_restarts:
                logger.warning(
                    "Worker for %s crashed (exitcode=%d) — restarting (%d/%d)",
                    project_path, proc.exitcode, restart_count + 1, self._max_restarts,
                )
                self._restart_counts[project_path] = restart_count + 1
                self._put_notification("worker_restart", project_name, {
                    "restart_count": restart_count + 1,
                    "max_restarts": self._max_restarts,
                })
                self._spawn_worker(project_path)
            else:
                logger.error(
                    "Worker for %s exceeded max_restarts=%d. Giving up.",
                    project_path, self._max_restarts,
                )
                self._put_notification("worker_failed", project_name, {
                    "max_restarts": self._max_restarts,
                })

    def _put_notification(self, event: str, project: str, data: dict[str, Any]) -> None:
        try:
            self._notification_queue.put_nowait({"event": event, "project": project, "data": data})
        except Exception:
            pass

    def _terminate_workers(self, timeout: float = 30.0) -> None:
        for proc in self._workers.values():
            if proc.is_alive():
                proc.terminate()
        deadline = time.monotonic() + timeout
        for proc in self._workers.values():
            remaining = max(0.0, deadline - time.monotonic())
            proc.join(timeout=remaining)
            if proc.is_alive():
                proc.kill()

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info("MultiOrchid: received signal %d — stopping…", signum)
        self.stop()
