"""Subprocess task isolation runner.

Two modes:
- One-shot (legacy): fork a fresh Python interpreter per task.
- Pool mode: a pre-forked worker pool avoids interpreter startup overhead.

Pool is used when isolation.subprocess_workers > 0 (default: match max_parallel).
Resource limits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_NOFILE) applied in child after fork.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult

logger = logging.getLogger(__name__)


def _child_cpu() -> float:
    """Return cumulative child CPU seconds (user + sys) for RUSAGE_CHILDREN."""
    try:
        import resource as _res
        u = _res.getrusage(_res.RUSAGE_CHILDREN)
        return u.ru_utime + u.ru_stime
    except Exception:
        return 0.0


# ── Resource limits ───────────────────────────────────────────────────────────


def _apply_resource_limits(cfg: Any) -> None:
    """Apply OS resource limits to the current process (call in child after fork)."""
    try:
        import resource as _res
        limits = cfg.get("isolation.resource_limits", {}) or {}

        as_gb = limits.get("max_as_gb", 4)
        if as_gb:
            try:
                _res.setrlimit(_res.RLIMIT_AS, (int(as_gb) * 1024 ** 3, _res.RLIM_INFINITY))
            except Exception as e:
                logger.debug("RLIMIT_AS: %s", e)

        cpu_s = limits.get("max_cpu_s", 600)
        if cpu_s:
            try:
                _res.setrlimit(_res.RLIMIT_CPU, (int(cpu_s), _res.RLIM_INFINITY))
            except Exception as e:
                logger.debug("RLIMIT_CPU: %s", e)

        max_files = limits.get("max_files", 256)
        if max_files:
            try:
                _res.setrlimit(_res.RLIMIT_NOFILE, (int(max_files), int(max_files)))
            except Exception as e:
                logger.debug("RLIMIT_NOFILE: %s", e)
    except ImportError:
        pass  # Windows


def _resource_preexec() -> None:
    """preexec_fn: apply resource limits from config in freshly forked child."""
    try:
        from orchid import config as cfg
        _apply_resource_limits(cfg)
    except Exception:
        pass


# ── Worker pool ───────────────────────────────────────────────────────────────


class _PoolWorker:
    """One pre-forked worker process that accepts tasks sequentially."""

    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "orchid.worker_subprocess", "--pool"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=_resource_preexec,
        )
        self._lock = threading.Lock()
        self._current_task_id: str | None = None  # set while run_task holds _lock
        self._is_suspended: bool = False
        # Wait for worker to signal it is ready
        self._ready = False
        self._wait_ready()

    def _wait_ready(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                break
            try:
                data = json.loads(line.strip())
                if data.get("type") == "ready":
                    self._ready = True
                    return
            except json.JSONDecodeError:
                pass
        logger.warning("Pool worker did not signal ready within %.1fs", timeout)

    def alive(self) -> bool:
        return self._proc.poll() is None

    def run_task(
        self,
        ctx: TaskContext,
        stream_callback: Callable[[dict], None] | None,
        timeout_s: float | None,
    ) -> WorkerResult:
        with self._lock:
            self._current_task_id = ctx.task_id
            self._is_suspended = False
            try:
                try:
                    self._proc.stdin.write(ctx.to_json() + "\n")
                    self._proc.stdin.flush()
                except OSError as e:
                    return WorkerResult(task_id=ctx.task_id, success=False,
                                        error=f"Worker stdin error: {e}")

                worker_result: WorkerResult | None = None
                deadline = time.monotonic() + (timeout_s or 3600.0)

                while time.monotonic() < deadline:
                    line = self._proc.stdout.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data: dict = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if data.get("type") == "ready":
                        # Worker finished this task and is ready for the next one
                        break
                    elif "success" in data:
                        worker_result = WorkerResult(**data)
                    elif stream_callback is not None:
                        stream_callback(data)

                if worker_result is None:
                    return WorkerResult(task_id=ctx.task_id, success=False,
                                        error="Worker exited without result")
                return worker_result
            finally:
                self._current_task_id = None
                self._is_suspended = False

    def suspend(self) -> None:
        """Send SIGSTOP to freeze the worker process."""
        try:
            os.kill(self._proc.pid, signal.SIGSTOP)
            self._is_suspended = True
        except OSError as e:
            logger.warning("suspend SIGSTOP failed: %s", e)

    def resume(self) -> None:
        """Send SIGCONT to unfreeze the worker process."""
        try:
            os.kill(self._proc.pid, signal.SIGCONT)
            self._is_suspended = False
        except OSError as e:
            logger.warning("resume SIGCONT failed: %s", e)

    def close(self) -> None:
        try:
            self._proc.stdin.write(json.dumps({"type": "exit"}) + "\n")
            self._proc.stdin.flush()
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


class WorkerPool:
    """Pre-forked pool of isolated worker processes."""

    def __init__(self, size: int) -> None:
        self._size = max(1, size)
        self._workers: list[_PoolWorker] = []
        self._semaphore = threading.Semaphore(self._size)
        self._lock = threading.Lock()
        self._closed = False
        self._spawn_workers()

    def _spawn_workers(self) -> None:
        for _ in range(self._size):
            try:
                w = _PoolWorker()
                self._workers.append(w)
            except Exception as e:
                logger.warning("Failed to spawn pool worker: %s", e)

    def _get_worker(self) -> _PoolWorker | None:
        with self._lock:
            for w in self._workers:
                if w.alive() and not w._lock.locked():
                    return w
            # All busy or dead — spawn a fresh replacement if needed
            dead = [w for w in self._workers if not w.alive()]
            for w in dead:
                self._workers.remove(w)
                try:
                    replacement = _PoolWorker()
                    self._workers.append(replacement)
                    return replacement
                except Exception as e:
                    logger.warning("Could not replace dead worker: %s", e)
        return None

    def submit(
        self,
        ctx: TaskContext,
        stream_callback: Callable[[dict], None] | None = None,
        timeout_s: float | None = None,
    ) -> WorkerResult:
        self._semaphore.acquire()
        try:
            worker = self._get_worker()
            if worker is None:
                return WorkerResult(task_id=ctx.task_id, success=False,
                                    error="No available pool worker")
            return worker.run_task(ctx, stream_callback, timeout_s)
        finally:
            self._semaphore.release()

    def _find_worker_for_task(self, task_id: str) -> _PoolWorker | None:
        with self._lock:
            for w in self._workers:
                if w._current_task_id == task_id:
                    return w
        return None

    def suspend_task(self, task_id: str) -> bool:
        w = self._find_worker_for_task(task_id)
        if w is None:
            return False
        w.suspend()
        return True

    def resume_task(self, task_id: str) -> bool:
        w = self._find_worker_for_task(task_id)
        if w is None:
            return False
        w.resume()
        return True

    def is_task_suspended(self, task_id: str) -> bool:
        w = self._find_worker_for_task(task_id)
        return w is not None and w._is_suspended

    def is_task_running(self, task_id: str) -> bool:
        return self._find_worker_for_task(task_id) is not None

    def shutdown(self) -> None:
        self._closed = True
        with self._lock:
            for w in self._workers:
                try:
                    w.close()
                except Exception:
                    pass
            self._workers.clear()


# Module-level singleton pool (created lazily)
_pool: WorkerPool | None = None
_pool_lock = threading.Lock()


def _get_pool(size: int) -> WorkerPool:
    global _pool
    if _pool is None or _pool._closed:
        with _pool_lock:
            if _pool is None or _pool._closed:
                _pool = WorkerPool(size)
                logger.info("WorkerPool started (size=%d)", size)
    return _pool


def pool_suspend_task(task_id: str) -> bool:
    """Suspend a task running in the pool via SIGSTOP. Returns True if found."""
    if _pool is None or _pool._closed:
        return False
    return _pool.suspend_task(task_id)


def pool_resume_task(task_id: str) -> bool:
    """Resume a suspended pool task via SIGCONT. Returns True if found."""
    if _pool is None or _pool._closed:
        return False
    return _pool.resume_task(task_id)


def pool_is_suspended(task_id: str) -> bool:
    if _pool is None or _pool._closed:
        return False
    return _pool.is_task_suspended(task_id)


def pool_is_running(task_id: str) -> bool:
    if _pool is None or _pool._closed:
        return False
    return _pool.is_task_running(task_id)


# ── SubprocessRunner ──────────────────────────────────────────────────────────


class SubprocessRunner:

    def run_task_isolated(
        self,
        ctx: TaskContext,
        stream_callback: Callable[[dict], None] | None = None,
        timeout_s: float | None = None,
    ) -> WorkerResult:
        from orchid import config as cfg

        if cfg.get("isolation.container_enabled", False):
            from orchid.container_runner import ContainerRunner
            _cr = ContainerRunner()
            if _cr.is_available():
                return _cr.run_task_isolated(ctx, stream_callback=stream_callback,
                                             timeout_s=timeout_s)
            logger.warning("container_enabled=true but docker not found — falling back to subprocess")

        pool_size = cfg.get("isolation.subprocess_workers", 0)
        if pool_size and pool_size > 0:
            return _get_pool(int(pool_size)).submit(ctx, stream_callback, timeout_s)

        return self._run_oneshot(ctx, stream_callback, timeout_s)

    def _run_oneshot(
        self,
        ctx: TaskContext,
        stream_callback: Callable[[dict], None] | None,
        timeout_s: float | None,
    ) -> WorkerResult:
        """Legacy one-shot: fork a fresh interpreter per task."""
        _cpu_before = _child_cpu()
        proc = subprocess.Popen(
            [sys.executable, "-m", "orchid.worker_subprocess"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=_resource_preexec,
        )

        proc.stdin.write(ctx.to_json() + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        worker_result: WorkerResult | None = None

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                data: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "success" in data:
                worker_result = WorkerResult(**{k: v for k, v in data.items()
                                               if k in WorkerResult.__dataclass_fields__})
            elif stream_callback is not None:
                stream_callback(data)

        try:
            proc.wait(timeout=int(timeout_s) if timeout_s else None)
        except subprocess.TimeoutExpired:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()
            return WorkerResult(task_id=ctx.task_id, success=False,
                                error=f"Worker timed out after {timeout_s}s")

        cpu_s = _child_cpu() - _cpu_before
        if worker_result is not None:
            worker_result.cpu_seconds = round(cpu_s, 3)
            return worker_result
        return WorkerResult(task_id=ctx.task_id, success=False,
                            error="Worker exited without result")
