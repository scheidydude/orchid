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

        system_prompt = f"""You are a task setup wizard for Orchid, a scheduling platform. Help the user configure a scheduled task through a short, friendly conversation. Ask 1-2 questions at a time and keep replies brief.

TASK TYPES:
- agent_prompt: AI generates text (summaries, reports, emails). Needs: prompt, optional system.
- agent_tool: AI uses MCP tools to accomplish a goal. Needs: servers list, prompt, optional system.
- mcp_tool: Single direct tool call. Needs: server name, tool name, args object.
- shell: Run a shell command. Needs: command string, optional timeout_sec.

AVAILABLE MCP SERVERS:
{mcp_context}

SCHEDULE (cron, literal — no timezone conversion):
  daily at 9am → 0 9 * * *   |  weekdays at 9am → 0 9 * * 1-5
  every hour → 0 * * * *     |  every 15 min → */15 * * * *
  weekly Mon 9am → 0 9 * * 1 |  monthly 1st 9am → 0 9 1 * *

Ask questions until you know: (1) what the task does, (2) when it runs, (3) any specific prompt/command/tool details.
When you have everything needed, end your reply with the single word TASK_READY on its own line. Nothing after it."""

        if not messages:
            raise HTTPException(status_code=400, detail="messages must not be empty")

        import asyncio as _asyncio

        registry = _get_registry()
        provider = registry.resolve(agent_type="base")

        loop = _asyncio.get_running_loop()
        content = (await loop.run_in_executor(
            None,
            lambda: provider.complete(messages=messages, system=system_prompt, max_tokens=500),
        )).strip()

        logger.debug("Wizard LLM raw output: %r", content)

        task_config = None

        if "TASK_READY" in content:
            display_content = content.split("TASK_READY")[0].strip()

            # Second call: flatten conversation into one user message so the
            # LLM does extraction, not conversation-following (local LLMs copy
            # template placeholders when given a message array + JSON template).
            convo_text = "\n".join(
                f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
                for m in messages
            )
            if display_content:
                convo_text += f"\nAssistant: {display_content}"
            # Build server name list for extraction guidance
            server_names = [s["server"] for s in mcp_servers if "server" in s] if mcp_servers else []
            servers_hint = (
                f"Available MCP servers: {server_names}. "
                "If the task searches APIs, sends email, reads files, or calls external services "
                "it needs agent_tool with those servers — NOT agent_prompt. "
                f"agent_tool config: {{\"servers\":{server_names!r},\"prompt\":\"...\",\"system\":\"\"}}. "
                if server_names else ""
            )
            extraction_msgs = [
                {
                    "role": "user",
                    "content": (
                        "Read the conversation below and output ONLY a JSON object "
                        "for the scheduled task. No explanation, no markdown.\n\n"
                        f"{convo_text}\n\n"
                        "TASK TYPE RULES:\n"
                        "- agent_prompt: pure text generation, no external APIs or tools needed.\n"
                        "- agent_tool: task needs to search, fetch, send email, call APIs, or use any MCP server.\n"
                        "- mcp_tool: single direct tool call.\n"
                        "- shell: shell command.\n"
                        f"{servers_hint}"
                        "JSON fields:\n"
                        "- name: short 3-5 word task name\n"
                        "- description: one concise sentence summarising what the task does (NOT the user's words verbatim — write it as a system description, e.g. 'Emails a daily digest of top GitHub LLM repos.')\n"
                        "- schedule: cron expression\n"
                        "- task_type: see rules above\n"
                        "- config: task-specific config object\n"
                        "- enabled: true\n"
                        "- notify_on_failure: true\n"
                        "- notify_on_success: false\n"
                        "Output JSON:"
                    ),
                }
            ]
            extraction_sys = (
                "You extract scheduled task configuration from a conversation and output ONLY valid JSON. "
                "When a task involves searching, fetching data, sending email, or calling any external service, "
                "choose task_type=agent_tool and include a servers list."
            )
            try:
                json_raw = (await loop.run_in_executor(
                    None,
                    lambda: provider.complete(messages=extraction_msgs, system=extraction_sys, max_tokens=500),
                )).strip()
            except Exception as exc:
                logger.warning("Wizard extraction call failed: %s", exc)
                json_raw = ""

            logger.debug("Wizard extraction raw output: %r", json_raw)

            # Strip markdown fences if present
            if json_raw.startswith("```"):
                json_raw = json_raw.strip("`").strip()
                if json_raw.startswith("json"):
                    json_raw = json_raw[4:].strip()

            # Extract first complete JSON object via brace depth tracking
            brace_depth = 0
            end_idx = 0
            for idx, ch in enumerate(json_raw):
                if ch == "{":
                    brace_depth += 1
                elif ch == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        end_idx = idx + 1
                        break
            if end_idx:
                try:
                    task_config = _json.loads(json_raw[:end_idx])
                except _json.JSONDecodeError as exc:
                    logger.warning("Wizard JSON parse failed: %s — raw: %r", exc, json_raw[:end_idx])
        else:
            display_content = content

        return {"message": display_content, "task_config": task_config}

    app.add_api_route(
        "/api/scheduler/wizard",
        task_wizard,
        methods=["POST"],
    )
