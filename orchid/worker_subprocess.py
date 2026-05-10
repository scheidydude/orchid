"""Subprocess entry point for isolated task execution.

Two modes:
  One-shot (default): read one TaskContext from stdin, run, write WorkerResult, exit.
  Pool mode (--pool): signal ready, loop accepting tasks until {"type":"exit"}.
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


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    """One-shot mode: read one task, run, exit."""
    line = sys.stdin.readline()
    ctx = TaskContext.from_json(line)
    result = _run(ctx)
    sys.stdout.write(result.to_json() + "\n")
    sys.stdout.flush()


def pool_main() -> None:
    """Pool mode: signal ready, loop accepting tasks until exit message."""
    _write({"type": "ready"})

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if msg.get("type") == "exit":
            break

        try:
            ctx = TaskContext(**msg)
        except Exception as e:
            _write({"task_id": "", "success": False, "error": f"bad context: {e}",
                    "result": "", "duration_s": 0.0})
            _write({"type": "ready"})
            continue

        result = _run(ctx)
        sys.stdout.write(result.to_json() + "\n")
        sys.stdout.flush()
        _write({"type": "ready"})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--pool":
        pool_main()
    else:
        main()
