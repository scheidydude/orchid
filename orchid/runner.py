"""orchid/runner.py - Web-UI background runner.

Manages per-project auto-runs in background threads with **parallel
task dispatch** (T180).

Parallel dispatch (D0021):
  The scheduler identifies sets of independent tasks that can run
  concurrently.  Each parallel group is dispatched via a thread pool;
  tasks within a group execute simultaneously, limited by per-provider
  semaphores to prevent API-rate-limit explosions.

Provider semaphores (T179):
  Each provider name (e.g. "claude", "local", "ollama") gets its own
  threading.Semaphore, limiting how many tasks can call that provider
  concurrently across all projects.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchid.config import get as _cfg_get
from orchid.memory.state import Task, TaskStatus
from orchid.watchdog import TaskWatchdog

logger = logging.getLogger(__name__)


def _cfg(key_path: str, default: Any = None) -> Any:
    """Dot-separated key lookup, e.g. _cfg('models.claude.model')."""
    return _cfg_get(key_path, default)


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

        # -- Provider semaphores (T179) --
        # Maps provider name -> threading.Semaphore.
        # Default concurrency: 3 for "claude" (API rate limit), 10 for others.
        self._provider_concurrency: dict[str, int] = {
            "claude": 3,
            "openrouter": 3,
            "bedrock": 3,
            "openai": 3,
        }
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._sem_lock = threading.Lock()

    # -- Public API --

    def start(self, project_path: str) -> bool:
        """Start an auto-run for *project_path*. Returns False if already running."""
        with self._lock:
            state = self._states.get(project_path)
            if state and state.future and not state.future.done():
                return False
            state = _ProjectState()
            self._states[project_path] = state
            self._write_marker(project_path)
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

    def graceful_shutdown(self, timeout_s: float | None = None) -> bool:
        """Signal all running projects to stop and wait up to timeout_s for clean exit.

        Returns True if all runs finished within the timeout, False if any were
        still running when the timeout expired (they will be killed by the OS).
        Called by web_server lifespan on SIGTERM.
        """
        import orchid.shutdown as _shutdown
        if timeout_s is None:
            timeout_s = float(_cfg("runner.shutdown_timeout", 30))

        _shutdown.request_shutdown()

        with self._lock:
            states = dict(self._states)

        # Signal every project's cancel_event
        for state in states.values():
            state.cancel_event.set()

        # Wait for all futures; return False if any still running at deadline
        deadline = time.monotonic() + timeout_s
        all_done = True
        for project_path, state in states.items():
            if state.future is None or state.future.done():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("Shutdown timeout reached; some tasks may still be running")
                return False
            try:
                state.future.result(timeout=remaining)
            except TimeoutError:
                all_done = False
                logger.warning("Task in %s did not finish within shutdown timeout", project_path)
            except Exception:
                pass  # task raised an error, but it finished

        if all_done:
            logger.info("Graceful shutdown complete")
        return all_done

    # -- Orphan recovery (Phase 2) --

    @staticmethod
    def _marker_path(project_path: str) -> Path:
        return Path(project_path) / ".orchid" / "running"

    def _write_marker(self, project_path: str) -> None:
        p = self._marker_path(project_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(os.getpid()), encoding="utf-8")

    def _remove_marker(self, project_path: str) -> None:
        try:
            self._marker_path(project_path).unlink(missing_ok=True)
        except Exception:
            pass

    # -- Phase 4: suspend / resume --

    def suspend_task(self, task_id: str) -> bool:
        """Suspend a running task at its next iteration boundary. Returns True if found."""
        from orchid import agent_registry as _ar
        agent = _ar.get(task_id)
        if agent is None:
            return False
        agent.suspend()
        logger.info("[runner] Suspended task %s", task_id)
        return True

    def resume_task(self, task_id: str) -> bool:
        """Resume a suspended task. Returns True if found."""
        from orchid import agent_registry as _ar
        agent = _ar.get(task_id)
        if agent is None:
            return False
        agent.resume()
        logger.info("[runner] Resumed task %s", task_id)
        return True

    def is_suspended(self, task_id: str) -> bool:
        from orchid import agent_registry as _ar
        agent = _ar.get(task_id)
        return agent is not None and getattr(agent, "_suspended", False)

    # -- Orphan recovery (Phase 2) --

    def recover_orphans(self, project_path: str) -> int:
        """Check for tasks left IN_PROGRESS by a previous crash and re-queue them.

        Returns the number of tasks recovered.
        """
        if not self._marker_path(project_path).exists():
            return 0

        from orchid.checkpoint.restore import resume_orphaned_tasks
        count = resume_orphaned_tasks(project_path)
        self._remove_marker(project_path)
        if count:
            logger.info("[recovery] %d orphaned task(s) recovered in %s", count, project_path)
        return count

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

    # -- Provider semaphore helpers --

    def _get_semaphore(self, provider_name: str) -> threading.Semaphore:
        """Return (or lazily create) the semaphore for *provider_name*."""
        with self._sem_lock:
            if provider_name not in self._semaphores:
                max_conc = self._provider_concurrency.get(provider_name, 10)
                self._semaphores[provider_name] = threading.Semaphore(max_conc)
            return self._semaphores[provider_name]

    def set_provider_concurrency(self, provider_name: str, max_concurrent: int) -> None:
        """Update the concurrency limit for a provider. Creates the semaphore if needed."""
        with self._sem_lock:
            self._provider_concurrency[provider_name] = max_concurrent
            if provider_name not in self._semaphores:
                self._semaphores[provider_name] = threading.Semaphore(max_concurrent)
            else:
                # Adjust semaphore: drain excess permits or add new ones
                current = self._semaphores[provider_name]._value
                if max_concurrent > current:
                    for _ in range(max_concurrent - current):
                        self._semaphores[provider_name].release()
                elif max_concurrent < current:
                    for _ in range(current - max_concurrent):
                        self._semaphores[provider_name].acquire()

    # -- Internal --

    def _run(self, project_path: str, state: _ProjectState) -> None:
        from orchid.mcp.manager import MCPManager
        from orchid.orchestrator import Orchestrator
        from orchid.output.emitter import NullEmitter
        from orchid.output.events import SessionEndEvent, SessionStartEvent
        from orchid.session import Session

        session = Session(project_dir=project_path)
        session.load()
        orch = Orchestrator(session)

        mcp = MCPManager()
        mcp.discover_servers()
        try:
            mcp.connect()
        except Exception:
            logger.warning("MCP server connection failed for %s", project_path)

        # Session-level stream emitter
        emitter = NullEmitter()
        session_start_ts = time.monotonic()

        # Try to wire into the web-stream emitter if one is registered
        from orchid.web.server import _stream_emitters
        web_emitter = _stream_emitters.get(project_path)
        if web_emitter is not None:
            emitter = web_emitter

        # Emit session start event
        emitter.emit(SessionStartEvent(
            session_id=session.project_name,
            project=project_path,
            mode="auto",
        ))

        try:
            self._run_loop(project_path, state, session, orch, emitter)
        except Exception:
            logger.exception("Auto-run failed for %s", project_path)
        finally:
            state.current_task = None
            self._remove_marker(project_path)
            try:
                mcp.disconnect()
            except NameError:
                pass

            # Prune old checkpoints at session end
            from orchid.checkpoint.store import CheckpointStore
            try:
                store = CheckpointStore(project_path)
                store.prune(keep=5)
            except Exception:  # noqa: BLE001
                logger.warning("Checkpoint pruning failed for %s", project_path)

        # Emit session end event
        duration_s = time.monotonic() - session_start_ts
        emitter.emit(SessionEndEvent(
            session_id=session.project_name,
            task_count=state.tasks_done,
            duration_s=round(duration_s, 2),
        ))
        emitter.close()

    def _run_loop(
        self,
        project_path: str,
        state: _ProjectState,
        session: Session,
        orch: Orchestrator,
        emitter: Any,
    ) -> None:
        """Main parallel dispatch loop (T180).

        Uses the Scheduler to identify parallel groups of independent tasks.
        Each group is dispatched concurrently via a thread pool; after all
        tasks in a group complete, the loop re-schedules to find the next
        batch.  This repeats until no more TODO tasks remain or the cancel
        event is set.
        """
        from pathlib import Path

        from orchid.scheduler import Scheduler

        scheduler = Scheduler(session.tasks)
        completed_ids: set[str] = set()

        # T219: Start stuck-task watchdog
        _watchdog_threshold = _cfg("isolation.watchdog_threshold_s", 1800)
        _watchdog = TaskWatchdog(session, stuck_threshold_s=_watchdog_threshold)
        _watchdog.start()

        # T270: Build RemoteDispatcher if remote.enabled
        _remote_dispatcher = None
        if _cfg("remote.enabled", False):
            from orchid.remote.dispatcher import RemoteDispatcher
            from orchid.remote.types import WorkerNode
            _raw_nodes = _cfg("remote.nodes", [])
            _nodes = [WorkerNode(**n) for n in _raw_nodes]
            if _nodes:
                _remote_dispatcher = RemoteDispatcher(_nodes)
                logger.info("[runner] Remote dispatch enabled: %d nodes", len(_nodes))

        try:
            while not state.cancel_event.is_set():
                # Re-schedule to pick up the next parallel group
                result = scheduler.schedule(completed_ids=completed_ids)

                # No more runnable tasks
                if not result.parallel_groups and not result.ordered:
                    break

                # Execute the next parallel group
                group = result.parallel_groups[0] if result.parallel_groups else [result.ordered[0]]

                if not group:
                    break

                logger.info(
                    "Dispatching parallel group of %d task(s) for %s",
                    len(group), project_path,
                )

                # Execute tasks in this group concurrently
                self._execute_group(project_path, state, session, orch, group, completed_ids)

                # T270: Merge remote ledger if enabled
                if _remote_dispatcher is not None and _cfg("remote.merge_ledger_after_group", True):
                    try:
                        _ledger_path = Path(project_path) / ".orchid" / "cost_ledger.jsonl"
                        _merged = _remote_dispatcher.fetch_and_merge_ledger(_ledger_path)
                        if _merged:
                            logger.info("[runner] Merged %d cost ledger lines from remote nodes", _merged)
                    except Exception as _re:
                        logger.warning("[runner] Remote ledger merge failed: %s", _re)

                # Update completed_ids from actual task statuses so the next schedule()
                # call can correctly resolve dependencies for tasks whose parents just
                # finished. Without this, tasks with completed parents are never ready.
                completed_ids = {
                    t.id for t in session.tasks
                    if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED)
                }

                # Rebuild scheduler with updated task statuses
                scheduler.reset_cache()
        finally:
            _watchdog.stop()

    def _execute_group(
        self,
        project_path: str,
        state: _ProjectState,
        session: Session,
        orch: Orchestrator,
        group: list[Task],
        completed_ids: set[str],
    ) -> None:
        """Execute a group of tasks in parallel via ThreadPoolExecutor.

        Each task is executed in its own thread.  Provider semaphores
        limit concurrent API calls per provider.  The group's futures
        are awaited before the loop continues.
        """
        from orchid import config as _cfg_mod

        # Create a local thread pool for this group's parallel dispatch
        max_workers = min(len(group), _cfg_mod.get("runner.max_parallel", 4))
        group_executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"orchid-group-{project_path}",
        )

        futures: dict[Future, Task] = {}
        for task in group:
            state.current_task = f"{task.id}: {task.title}"
            future = group_executor.submit(
                self._execute_task_with_semaphore,
                project_path,
                state,
                session,
                orch,
                task,
            )
            futures[future] = task

        # Wait for all tasks in the group to complete
        for future in as_completed(futures):
            task = futures[future]
            try:
                future.result()
            except Exception:
                logger.exception("Task %s failed in parallel group", task.id)

        group_executor.shutdown(wait=True)

    def _execute_task_with_semaphore(
        self,
        project_path: str,
        state: _ProjectState,
        session: Session,
        orch: Orchestrator,
        task: Task,
    ) -> None:
        """Execute a single task, wrapped in the provider semaphore.

        This is the work function submitted to the group executor.
        """
        # Acquire the provider semaphore before executing the task.
        # This limits concurrent calls to the same provider across
        # all running projects, preventing API rate-limit issues.
        provider_name = getattr(task, "model_override", None) or "local"
        semaphore = self._get_semaphore(provider_name)

        try:
            with semaphore:
                try:
                    orch._execute_task(task)
                    session.save()
                    state.tasks_done += 1
                except Exception:
                    logger.exception("Task %s failed", task.id)
                    session.update_task_status(task.id, TaskStatus.BLOCKED)
                    session.save()
        except Exception:
            logger.exception("Semaphore acquisition failed for %s", task.id)
            session.update_task_status(task.id, TaskStatus.BLOCKED)
            session.save()