# Orchid Cron — Engine (APScheduler BackgroundScheduler wrapper)

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from orchid.cron.executor import TaskExecutor
from orchid.cron.store import TaskRunStore

logger = logging.getLogger(__name__)


class CronEngine:
    """Manages scheduled task dispatch via APScheduler BackgroundScheduler.

    Each user can have multiple scheduled tasks; the engine maintains a
    per-user ``APScheduler`` instance so that tasks are scoped to owners.
    The engine also supports ad-hoc ``run_now`` calls that execute in a
    background thread without touching the scheduler.
    """

    def __init__(self) -> None:
        self._executor = TaskExecutor()
        self._run_store = TaskRunStore()
        self._lock = threading.Lock()
        # user_id → APScheduler BackgroundScheduler
        self._schedulers: dict[str, object] = {}
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._SchedulerCls = BackgroundScheduler  # type: ignore[misc]
        except ImportError:
            logger.warning("APScheduler not installed; scheduler features disabled")
            self._SchedulerCls = None

    def _get_scheduler(self, user_id: str) -> object | None:
        """Return (or create) the per-user BackgroundScheduler."""
        if self._SchedulerCls is None:
            return None
        with self._lock:
            if user_id not in self._schedulers:
                sched = self._SchedulerCls(daemon=True)
                try:
                    sched.start()
                except Exception:
                    logger.warning("Failed to start scheduler for %s", user_id)
                    return None
                self._schedulers[user_id] = sched
            return self._schedulers[user_id]
    def _parse_cron_expression(self, expr: str) -> dict | None:
        """Parse a 5-field cron expression into APScheduler keyword args.

        Returns None if the expression is not a valid 5-field cron string,
        so that callers can skip registration entirely.
        """
        parts = expr.strip().split()
        if len(parts) != 5:
            return None
        minute, hour, day, month, dow = parts
        kwargs: dict = {}
        for field_name, value in [("minute", minute), ("hour", hour),
                                   ("day", day), ("month", month),
                                   ("day_of_week", dow)]:
            if value != "*":
                kwargs[field_name] = value
        return kwargs

    def add_or_update_task(self, owner_id: str, task_dict: dict) -> None:
        """Register or replace a cron job in the per-user scheduler.

        If *task_dict* has ``enabled=False`` (or is absent), the existing
        trigger for this task_id is removed instead of added.
        """
        sched = self._get_scheduler(owner_id)
        if sched is None:
            return

        task_type = task_dict.get("task_type", "")
        config = task_dict.get("config", {})
        schedule_str = task_dict.get("schedule", "0 9 * * *")
        enabled = task_dict.get("enabled", True)
        task_id = task_dict.get("task_id", "")

        # Remove any existing trigger first (idempotent)
        try:
            sched.remove_job(task_id)
        except Exception:
            pass

        if not enabled or task_type == "":
            return

        def _job() -> None:
            run = self._executor.execute(task_dict, owner_id)
            self._run_store.append(run)
            logger.info(
                "Cron run %s for task %s: status=%s",
                run.run_id, task_id, run.status,
            )

        cron_kwargs = self._parse_cron_expression(schedule_str)
        if cron_kwargs is None:
            logger.warning("Invalid cron expression for task %s: %s", task_id, schedule_str)
            return
        try:
            sched.add_job(
                _job,
                "cron",
                id=task_id,
                args=[],
                replace_existing=True,
                **cron_kwargs,
            )
        except Exception as exc:
            logger.warning("Failed to add cron job %s: %s", task_id, exc)
    def remove_task(self, task_id: str) -> None:
        """Remove a scheduled trigger by *task_id* from all schedulers."""
        with self._lock:
            for user_id, sched in list(self._schedulers.items()):
                try:
                    sched.remove_job(task_id)
                except Exception:
                    pass

    def run_now(self, owner_id: str, task_dict: dict) -> None:
        """Dispatch a single execution of *task_dict* in a background thread.

        This is fire-and-forget; the caller returns immediately.
        The result is recorded via ``TaskRunStore.append``.
        """
        import threading as _threading

        def _run() -> None:
            self._run_task(owner_id, task_dict)

        _threading.Thread(target=_run, daemon=True).start()

    def start(self) -> None:
        """Start the engine and register all enabled scheduled tasks.

        Reads enabled tasks from the auth store and adds them to the
        per-user schedulers.  Invalid cron expressions are silently skipped.
        """
        try:
            from orchid.auth.store import get_store
        except ImportError:
            logger.warning("orchid.auth.store not available; no tasks loaded at start")
            return

        store = get_store()
        for user_id, task_dict in store.get_all_enabled_scheduled_tasks():
            self.add_or_update_task(user_id, task_dict)

    def stop(self) -> None:
        """Stop all per-user schedulers and clear them."""
        with self._lock:
            for user_id, sched in list(self._schedulers.items()):
                try:
                    sched.shutdown(wait=False)
                except Exception:
                    pass
            self._schedulers.clear()

    def _run_task(self, owner_id: str, task_dict: dict) -> None:
        """Execute *task_dict* for *owner_id* and record the run.

        This method is called by both ``run_now`` (in a thread) and by
        APScheduler job callbacks.  It can be patched in tests to avoid
        real execution.
        """
        run = self._executor.execute(task_dict, owner_id)
        self._run_store.append(run)
        logger.info(
            "Run %s for task %s: status=%s",
            run.run_id, task_dict.get("task_id"), run.status,
        )
        # Dispatch notifications (Phase 2) — fire-and-forget, never raises
        try:
            from orchid.auth.notifications import dispatch_task_notification
            dispatch_task_notification(owner_id, task_dict, run)
        except Exception:
            pass

# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------

_engine_instance: CronEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> CronEngine:
    """Return the process-wide ``CronEngine`` singleton."""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = CronEngine()
    return _engine_instance


def reset_engine() -> None:
    """Destroy the process-wide ``CronEngine`` singleton.

    Call this in test teardowns to guarantee a fresh instance on next
    ``get_engine()`` call.
    """
    global _engine_instance
    if _engine_instance is not None:
        try:
            _engine_instance.stop()
        except Exception:
            pass
        _engine_instance = None
