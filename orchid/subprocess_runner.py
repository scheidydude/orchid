import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult

logger = logging.getLogger(__name__)


class SubprocessRunner:

    def run_task_isolated(
        self,
        ctx: TaskContext,
        stream_callback: Callable[[dict], None] | None = None,
        timeout_s: float | None = None,
    ) -> WorkerResult:
        """Run a task in an isolated subprocess and collect the result.

        The parent writes the serialized TaskContext to the worker's stdin,
        then reads JSON events from stdout line-by-line.
        """
        from orchid import config as cfg
        if cfg.get("isolation.container_enabled", False):
            from orchid.container_runner import ContainerRunner
            _cr = ContainerRunner()
            if _cr.is_available():
                return _cr.run_task_isolated(ctx, stream_callback=stream_callback, timeout_s=timeout_s)
            else:
                logger.warning("container_enabled=true but docker not found — falling back to subprocess")

        proc = subprocess.Popen(
            [sys.executable, "-m", "orchid.worker_subprocess"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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
                worker_result = WorkerResult(**data)
            elif stream_callback is not None:
                stream_callback(data)

        try:
            proc.wait(timeout=int(timeout_s) if timeout_s else None)
        except subprocess.TimeoutExpired:
            # SIGTERM first — gives the child a chance to save its ReAct checkpoint
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()
            worker_result = WorkerResult(
                task_id=ctx.task_id,
                success=False,
                error=f"Worker timed out after {timeout_s}s",
            )

        if worker_result is not None:
            return worker_result

        return WorkerResult(
            task_id=ctx.task_id,
            success=False,
            error="Worker exited without result",
        )