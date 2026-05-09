"""Subprocess entry point for isolated task execution (T210).

Run by the parent process via: sys.executable -m orchid.worker_subprocess
Reads one TaskContext JSON line from stdin, runs the agent, writes WorkerResult to stdout.
"""
import json
import logging
import sys
import time
from pathlib import Path

from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult

logger = logging.getLogger(__name__)


def _make_emit(task_id: str):
    def emit(payload: dict) -> None:
        event = WorkerEvent(
            type=payload.get("action", "agent_step"),
            task_id=task_id,
            payload=payload,
        )
        sys.stdout.write(event.to_json() + "\n")
        sys.stdout.flush()
    return emit


def _run(ctx: TaskContext) -> WorkerResult:
    from orchid.orchestrator import _get_registry
    start = time.monotonic()
    try:
        registry = _get_registry()
        agent_cls = registry.get(ctx.agent_type, registry["base"])
        agent = agent_cls(
            session_context=ctx.session_context,
            project_dir=Path(ctx.project_dir),
            stream_callback=_make_emit(ctx.task_id),
            injection_queue_path=Path(ctx.injection_queue_path),
        )
        agent.model_key = ctx.model_key
        result = agent.run(ctx.task_description)
        return WorkerResult(
            task_id=ctx.task_id,
            success=True,
            result=result,
            duration_s=time.monotonic() - start,
        )
    except Exception as e:
        return WorkerResult(
            task_id=ctx.task_id,
            success=False,
            error=str(e),
            duration_s=time.monotonic() - start,
        )


def main() -> None:
    line = sys.stdin.readline()
    ctx = TaskContext.from_json(line)
    result = _run(ctx)
    sys.stdout.write(result.to_json() + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
