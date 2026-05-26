# Orchid Cron — Scheduler API routes

import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def register_routes(app: Any) -> None:
    """Install all ``/api/scheduler/*`` endpoints on the given FastAPI *app*.

    Framework imports are performed locally inside this function so that
    importing this module does not require FastAPI to be installed at
    import time.  If any import fails a warning is logged and no routes
    are registered — the function never raises.
    """

    # ── framework / orchid imports (local, guarded) ───────────────────────
    try:
        from fastapi import Depends, HTTPException, Request  # noqa: F401
        from orchid.auth.middleware import require_auth  # noqa: F401
        from orchid.auth.store import get_store  # noqa: F401
        from orchid.cron.types import ScheduledTask  # noqa: F401
        from orchid.cron.store import TaskRunStore  # noqa: F401
        from orchid.cron.engine import get_engine  # noqa: F401
    except ImportError as exc:
        logger.warning("Cron API imports failed (%s); routes not registered", exc)
        return

    # ── local helpers (captured by endpoint closures) ─────────────────────

    _run_store = TaskRunStore()

    def _task_to_dict(task_dict: dict) -> dict:
        """Return a copy of *task_dict* with datetime fields as ISO strings."""
        out = dict(task_dict)
        for key in ("created_at", "last_run_at", "next_run_at"):
            val = out.get(key)
            if isinstance(val, datetime):
                out[key] = val.isoformat()
        return out

    def _run_to_dict(run) -> dict:
        """Convert a ``TaskRun`` dataclass instance to a plain dict with ISO datetimes."""
        d = dataclasses.asdict(run)
        for key in ("started_at", "finished_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        return d

    def _validate_task_body(body: dict) -> list[str]:
        """Return a list of error strings (empty == valid)."""
        errors: list[str] = []
        if not body.get("name", "").strip():
            errors.append("'name' is required and must be non-empty")
        if body.get("task_type", "") not in {"agent_prompt", "mcp_tool", "shell"}:
            errors.append("'task_type' must be one of: agent_prompt, mcp_tool, shell")
        if not body.get("schedule", "").strip():
            errors.append("'schedule' is required (cron expression, e.g. '0 9 * * *')")
        if not isinstance(body.get("config"), dict):
            errors.append("'config' must be a dict")
        return errors

    def _find_task_for_user(task_id: str, current_user) -> tuple[dict, str]:
        """Locate a scheduled task accessible to *current_user*.

        Returns ``(task_dict, owner_id)`` or raises ``HTTPException(404)``.
        """
        store = get_store()
        if current_user.role == "admin":
            for user in store.list_users():
                for t in user.scheduled_tasks:
                    if t.get("task_id") == task_id:
                        return (dict(t), user.user_id)
            raise HTTPException(status_code=404, detail="Task not found")
        else:
            task = store.get_scheduled_task(current_user.user_id, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found")
            return (task, current_user.user_id)

    # ── endpoint: GET /api/scheduler/tasks ────────────────────────────────

    async def list_tasks(current_user = Depends(require_auth())):
        """List all scheduled tasks visible to the caller."""
        store = get_store()
        if current_user.role == "admin":
            tasks = [t for user in store.list_users() for t in user.scheduled_tasks]
        else:
            user = store.get_user(current_user.user_id)
            tasks = user.scheduled_tasks if user else []
        return {
            "tasks": [_task_to_dict(t) for t in tasks],
            "total": len(tasks),
        }

    app.add_api_route("/api/scheduler/tasks", list_tasks, methods=["GET"])

    # ── endpoint: POST /api/scheduler/tasks ───────────────────────────────

    async def create_task(request: Request, current_user = Depends(require_auth())):
        """Create a new scheduled task."""
        body = await request.json()
        errors = _validate_task_body(body)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

        import uuid  # noqa: F811

        task_dict = {
            "name": body.get("name", ""),
            "description": body.get("description", ""),
            "enabled": body.get("enabled", True),
            "schedule": body.get("schedule", ""),
            "task_type": body.get("task_type", ""),
            "config": body.get("config", {}),
            "notify_on_failure": body.get("notify_on_failure", True),
            "notify_on_success": body.get("notify_on_success", False),
        }
        task_dict["task_id"] = f"stask_{uuid.uuid4().hex[:8]}"
        task_dict["owner_id"] = current_user.user_id
        task_dict["created_at"] = datetime.now(UTC).isoformat()
        task_dict["last_run_at"] = None
        task_dict["last_run_status"] = None
        task_dict["next_run_at"] = None

        store = get_store()
        store.upsert_scheduled_task(current_user.user_id, task_dict)

        if task_dict.get("enabled", True):
            engine = get_engine()
            engine.add_or_update_task(current_user.user_id, task_dict)

        return _task_to_dict(task_dict)

    app.add_api_route(
        "/api/scheduler/tasks",
        create_task,
        methods=["POST"],
        status_code=201,
    )

    # ── endpoint: GET /api/scheduler/tasks/{task_id} ──────────────────────

    async def get_task(task_id: str, current_user = Depends(require_auth())):
        """Retrieve a single scheduled task."""
        task, _owner_id = _find_task_for_user(task_id, current_user)
        return _task_to_dict(task)

    app.add_api_route(
        "/api/scheduler/tasks/{task_id}",
        get_task,
        methods=["GET"],
    )

    # ── endpoint: PUT /api/scheduler/tasks/{task_id} ──────────────────────

    async def update_task(
        task_id: str, request: Request, current_user = Depends(require_auth()),
    ):
        """Update an existing scheduled task."""
        body = await request.json()
        existing_task, owner_id = _find_task_for_user(task_id, current_user)

        errors = _validate_task_body(body)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

        updated = dict(existing_task)
        for key in (
            "name", "description", "enabled", "schedule",
            "task_type", "config", "notify_on_failure", "notify_on_success",
        ):
            if key in body:
                updated[key] = body[key]

        store = get_store()
        store.upsert_scheduled_task(owner_id, updated)

        engine = get_engine()
        engine.add_or_update_task(owner_id, updated)

        return _task_to_dict(updated)

    app.add_api_route(
        "/api/scheduler/tasks/{task_id}",
        update_task,
        methods=["PUT"],
    )

    # ── endpoint: DELETE /api/scheduler/tasks/{task_id} ───────────────────

    async def delete_task(task_id: str, current_user = Depends(require_auth())):
        """Delete a scheduled task."""
        _task, owner_id = _find_task_for_user(task_id, current_user)

        store = get_store()
        store.delete_scheduled_task(owner_id, task_id)

        engine = get_engine()
        engine.remove_task(task_id)

        return {"deleted": True, "task_id": task_id}

    app.add_api_route(
        "/api/scheduler/tasks/{task_id}",
        delete_task,
        methods=["DELETE"],
    )

    # ── endpoint: POST /api/scheduler/tasks/{task_id}/run ─────────────────

    async def run_task_now(task_id: str, current_user = Depends(require_auth())):
        """Trigger an immediate (asynchronous) execution of the task."""
        task, owner_id = _find_task_for_user(task_id, current_user)
        engine = get_engine()
        engine.run_now(owner_id, task)
        return {"queued": True, "task_id": task_id}

    app.add_api_route(
        "/api/scheduler/tasks/{task_id}/run",
        run_task_now,
        methods=["POST"],
    )

    # ── endpoint: GET /api/scheduler/tasks/{task_id}/runs ─────────────────

    async def get_task_runs(
        task_id: str,
        limit: int = 50,
        current_user = Depends(require_auth()),
    ):
        """List run history for a single scheduled task."""
        _task, _owner_id = _find_task_for_user(task_id, current_user)
        runs = _run_store.get_runs(task_id=task_id, limit=limit)
        return {
            "runs": [_run_to_dict(r) for r in runs],
            "total": len(runs),
        }

    app.add_api_route(
        "/api/scheduler/tasks/{task_id}/runs",
        get_task_runs,
        methods=["GET"],
    )

    # ── endpoint: GET /api/scheduler/runs ─────────────────────────────────

    async def list_runs(
        limit: int = 50,
        current_user = Depends(require_auth()),
    ):
        """List run history (all tasks for admin, own tasks otherwise)."""
        if current_user.role == "admin":
            runs = _run_store.get_runs(limit=limit)
        else:
            runs = _run_store.get_runs(owner_id=current_user.user_id, limit=limit)
        return {
            "runs": [_run_to_dict(r) for r in runs],
            "total": len(runs),
        }

    app.add_api_route("/api/scheduler/runs", list_runs, methods=["GET"])
