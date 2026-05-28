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
        if body.get("task_type", "") not in {"agent_prompt", "agent_tool", "mcp_tool", "shell"}:
            errors.append("'task_type' must be one of: agent_prompt, agent_tool, mcp_tool, shell")
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

    # ── endpoint: GET /api/scheduler/mcp-tools ────────────────────────────

    async def list_mcp_tools(current_user = Depends(require_auth())):
        """Return MCP tools available to the scheduler, grouped by server.

        Each server entry has ``{server, tools, error}`` where *error* is set
        if the server could not be reached.  All servers are probed concurrently;
        each connect runs in a thread with a 30-second timeout so a hanging
        subprocess (e.g. npx downloading) never blocks the endpoint.
        """
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from orchid.mcp.manager import MCPManager

        TIMEOUT = 30.0  # seconds per server

        mgr = MCPManager()
        mgr.discover_servers()

        def _probe(server_name: str, adapter) -> dict:
            """Synchronous connect+list+disconnect — run in a thread."""
            try:
                adapter.connect()
                raw = adapter.list_tools()
                return {
                    "server": server_name,
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in raw
                    ],
                    "error": None,
                }
            except Exception as exc:
                return {"server": server_name, "tools": [], "error": str(exc)}
            finally:
                try:
                    adapter.disconnect()
                except Exception:
                    pass

        async def _probe_async(server_name: str, adapter) -> dict:
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor(max_workers=1) as exe:
                try:
                    return await asyncio.wait_for(
                        loop.run_in_executor(exe, _probe, server_name, adapter),
                        timeout=TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    try:
                        adapter.disconnect()
                    except Exception:
                        pass
                    return {
                        "server": server_name,
                        "tools": [],
                        "error": f"Timed out after {TIMEOUT:.0f}s — server may still be starting",
                    }

        tasks = [
            _probe_async(name, adapter)
            for name, adapter in mgr._adapters.items()
        ]
        servers: list[dict] = list(await asyncio.gather(*tasks))

        total_tools = sum(len(s["tools"]) for s in servers)
        return {"servers": servers, "total_tools": total_tools}

    app.add_api_route(
        "/api/scheduler/mcp-tools",
        list_mcp_tools,
        methods=["GET"],
    )

    # ── endpoint: POST /api/scheduler/wizard ──────────────────────────────

    async def task_wizard(request: Request, current_user = Depends(require_auth())):
        """LLM-powered conversational task configuration wizard.

        Accepts ``{messages, mcp_servers, timezone}`` and returns
        ``{message, task_config}`` where ``task_config`` is non-null only
        when the LLM has collected enough information to build the full
        task definition.
        """
        import json as _json
        import os

        try:
            from orchid.providers.registry import get_registry as _get_registry
        except ImportError:
            raise HTTPException(status_code=503, detail="Provider registry not available")

        body = await request.json()
        messages = body.get("messages", [])   # [{role, content}] — alternating, starts with user
        mcp_servers = body.get("mcp_servers", [])
        timezone = body.get("timezone", "UTC")

        # Build MCP context string for the system prompt
        if mcp_servers:
            mcp_lines = []
            for s in mcp_servers:
                tools = [t["name"] for t in s.get("tools", [])]
                tools_str = ", ".join(tools) if tools else "no tools discovered"
                err = s.get("error")
                suffix = f" ⚠ {err}" if err else ""
                mcp_lines.append(f"  - {s['server']}: {tools_str}{suffix}")
            mcp_context = "\n".join(mcp_lines)
        else:
            mcp_context = "  (none configured)"

        system_prompt = f"""You are a friendly task configuration wizard for Orchid, an AI agent scheduling platform. Help the user set up a scheduled task through a short, natural conversation.

AVAILABLE TASK TYPES:
- agent_prompt  — Claude reads a prompt and generates output. Config: {{"prompt": "...", "system": "optional"}}
- agent_tool    — Claude uses one or more MCP servers as tools to accomplish a goal. Config: {{"servers": ["name"], "prompt": "...", "system": ""}}
- mcp_tool      — Directly calls one specific tool on one MCP server. Config: {{"server": "name", "tool": "tool_name", "args": {{}}}}
- shell         — Runs a shell command. Config: {{"command": "bash command", "timeout_sec": 60}}

AVAILABLE MCP SERVERS (for agent_tool / mcp_tool tasks):
{mcp_context}

USER'S TIMEZONE: {timezone}

SCHEDULE GUIDANCE — convert all times to UTC cron (5-field):
- every weekday at 9 am local → convert to UTC based on timezone offset
- "every hour"                → "0 * * * *"
- "every 15 minutes"          → "*/15 * * * *"
- "daily at midnight"         → "0 0 * * *"
- "weekly Monday 9 am"        → "0 9 * * 1" (then adjust for TZ)
- "monthly on the 1st"        → "0 9 1 * *"

CONVERSATION RULES:
1. Ask what the task should do (one concise question).
2. Ask when it should run — accept plain English; you will convert to cron.
3. Infer the best task_type from the description. Suggest MCP servers when relevant.
4. Gather remaining details (specific tool name, prompt text, shell command, etc.).
5. Ask at most TWO questions per reply. Keep replies short.
6. When you have all the information needed, output a short confirmation sentence followed immediately by the marker and JSON — nothing else after the closing brace.

OUTPUT FORMAT (when ready — do not include extra text after the JSON):
<TASK_READY>
{{"name":"Short task name","description":"One sentence","schedule":"cron expr","task_type":"one of the four types","config":{{}},"enabled":true,"notify_on_failure":true,"notify_on_success":false}}
"""

        if not messages:
            raise HTTPException(status_code=400, detail="messages must not be empty")

        import asyncio as _asyncio

        registry = _get_registry()
        provider = registry.resolve(agent_type="base")

        loop = _asyncio.get_running_loop()
        content = (await loop.run_in_executor(
            None,
            lambda: provider.complete(messages=messages, system=system_prompt, max_tokens=600),
        )).strip()

        task_config = None
        display_content = content

        if "<TASK_READY>" in content:
            parts = content.split("<TASK_READY>", 1)
            display_content = parts[0].strip()
            try:
                task_config = _json.loads(parts[1].strip())
            except (_json.JSONDecodeError, IndexError):
                pass

        return {"message": display_content, "task_config": task_config}

    app.add_api_route(
        "/api/scheduler/wizard",
        task_wizard,
        methods=["POST"],
    )
