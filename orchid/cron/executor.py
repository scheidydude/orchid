# Scheduler task executor
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from orchid.cron.types import TaskRun, _utcnow

logger = logging.getLogger(__name__)

_AGENT_TOOL_MAX_ITERS = 10
_AGENT_TOOL_MAX_TOKENS = 4096

# Phase 5: thread-local cost accumulator — set by execute(), read by _run_agent_tool
import threading
_exec_local = threading.local()


def _build_mcp_manager(owner_id: str) -> "MCPManager":
    """Return MCPManager with project config + catalog entries for *owner_id*."""
    from orchid.mcp.manager import MCPManager
    from orchid.mcp.adapter import MCPAdapter

    mgr = MCPManager()
    mgr.discover_servers()  # project mcp_servers config

    try:
        from orchid.mcp.catalog import get_catalog
        from orchid.auth.store import get_store

        store = get_store()
        user = store.get_user(owner_id)
        role = user.role if user else "user"

        for entry in get_catalog().get_servers_for_user(owner_id, role):
            if entry.server_id not in mgr._adapters:
                cfg = dict(entry.config)
                cfg["transport"] = entry.transport
                try:
                    mgr._adapters[entry.server_id] = MCPAdapter(
                        mgr._create_client(entry.server_id, cfg)
                    )
                except Exception as exc:
                    logger.warning("Skipping catalog server '%s': %s", entry.server_id, exc)
    except Exception as exc:
        logger.warning("Catalog merge failed in executor: %s", exc)

    return mgr


class TaskExecutionError(Exception):
    """Raised for known, non-retriable config errors."""


def _run_agent_prompt(config: dict, owner_id: str = "") -> str:
    """Execute an agent_prompt type task."""
    from orchid.providers.registry import get_registry

    prompt = config.get("prompt", "").strip()
    if not prompt:
        raise TaskExecutionError(
            "agent_prompt config missing required field: 'prompt'"
        )

    system_parts: list[str] = []

    sys_cfg = config.get("system", "").strip()
    if sys_cfg:
        system_parts.append(sys_cfg)

    mcp_servers = config.get("mcp_servers", [])
    if mcp_servers:
        try:
            mgr = _build_mcp_manager(owner_id)
            tool_lines: list[str] = []
            for server_name in mcp_servers:
                try:
                    adapter = mgr.get_adapter(server_name)
                    if adapter is None:
                        tool_lines.append(
                            f"  [server '{server_name}' not found in config]"
                        )
                    else:
                        adapter.connect()
                        tools = adapter.list_tools()
                        for t in tools:
                            tool_lines.append(f"  - {t.name}: {t.description}")
                        adapter.disconnect()
                except Exception as exc:
                    tool_lines.append(
                        f"  [server '{server_name}' error: {exc}]"
                    )
            if tool_lines:
                system_parts.append(
                    "Available MCP tools (reference only):\n" + "\n".join(tool_lines)
                )
        except Exception as exc:
            logger.warning("MCP discovery failed for agent_prompt: %s", exc)

    system = "\n\n".join(system_parts) if system_parts else None

    registry = get_registry()
    provider_name = config.get("provider", "").strip()
    if provider_name:
        provider = registry.get_by_key(provider_name)
    else:
        provider = registry.resolve(agent_type="base")

    result = provider.complete(
        [{"role": "user", "content": prompt}], system=system
    )
    return str(result)


def _run_mcp_tool(config: dict, owner_id: str = "") -> str:
    """Execute an mcp_tool type task."""
    server_name = config.get("server", "").strip()
    if not server_name:
        raise TaskExecutionError(
            "mcp_tool config missing required field: 'server'"
        )

    tool_name = config.get("tool", "").strip()
    if not tool_name:
        raise TaskExecutionError(
            "mcp_tool config missing required field: 'tool'"
        )

    args = config.get("args", {})
    if not isinstance(args, dict):
        raise TaskExecutionError(
            "mcp_tool config field 'args' must be a dict"
        )

    mgr = _build_mcp_manager(owner_id)
    adapter = mgr.get_adapter(server_name)
    if adapter is None:
        raise TaskExecutionError(
            f"MCP server '{server_name}' not found in config/catalog"
        )

    try:
        adapter.connect()
        result = adapter.call_tool(tool_name, args)
    finally:
        adapter.disconnect()

    if isinstance(result.content, list):
        parts: list[str] = []
        for item in result.content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", str(item))))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(result.content)


def _run_agent_tool(config: dict, owner_id: str = "") -> str:
    """Execute an agent_tool task: agentic loop with multiple MCP servers.

    Config fields:
        servers        list[str]  Required. MCP server names from catalog/config.
        prompt         str        Required. Initial user message.
        system         str        Optional. System prompt override.
        provider       str        Optional. Provider key (defaults to registry resolve).
        max_tokens     int        Optional. Max tokens per API call (default 4096).
        max_iterations int        Optional. Loop iteration cap (default 10).

    Tool names are prefixed ``server__toolname`` to avoid cross-server collisions.
    """
    from orchid.providers.registry import get_registry

    servers = config.get("servers", [])
    if not servers:
        raise TaskExecutionError("agent_tool config missing required field: 'servers'")

    prompt = config.get("prompt", "").strip()
    if not prompt:
        raise TaskExecutionError("agent_tool config missing required field: 'prompt'")

    max_iters  = int(config.get("max_iterations", _AGENT_TOOL_MAX_ITERS))
    max_tokens = int(config.get("max_tokens", _AGENT_TOOL_MAX_TOKENS))
    system     = config.get("system", "You are a helpful assistant.")

    registry = get_registry()
    provider_key = config.get("provider", "").strip()
    provider = registry.get_by_key(provider_key) if provider_key else registry.resolve(agent_type="base")

    mgr = _build_mcp_manager(owner_id)

    adapters: dict[str, object] = {}
    tool_defs: list[dict] = []
    tool_map: dict[str, tuple] = {}  # prefixed_name → (adapter, original_name)

    try:
        for server_name in servers:
            adapter = mgr.get_adapter(server_name)
            if adapter is None:
                raise TaskExecutionError(f"MCP server '{server_name}' not found in config/catalog")
            adapter.connect()
            adapters[server_name] = adapter
            for tool in adapter.list_tools():
                prefixed = f"{server_name}__{tool.name}"
                tool_defs.append({
                    "name": prefixed,
                    "description": tool.description,
                    "input_schema": tool.parameters or {"type": "object", "properties": {}},
                })
                tool_map[prefixed] = (adapter, tool.name)

        def dispatch(prefixed_name: str, args: dict) -> str:
            if prefixed_name not in tool_map:
                raise ValueError(f"unknown tool '{prefixed_name}'")
            adapter, original_name = tool_map[prefixed_name]
            result = adapter.call_tool(original_name, args)
            content = result.content
            if isinstance(content, list):
                parts = []
                for item in content:
                    parts.append(str(item.get("text", str(item))) if isinstance(item, dict) else str(item))
                content = "\n".join(parts)
            return str(content)

        return provider.complete_with_tools(
            messages=[{"role": "user", "content": prompt}],
            tools=tool_defs,
            dispatch_fn=dispatch,
            system=system,
            max_tokens=max_tokens,
            max_iterations=max_iters,
        )

    finally:
        for adapter in adapters.values():
            try:
                adapter.disconnect()
            except Exception:
                pass


def _run_shell(config: dict, owner_id: str = "") -> str:
    """Execute a shell type task."""
    from orchid.tools.shell import bash

    command = config.get("command", "").strip()
    if not command:
        raise TaskExecutionError(
            "shell config missing required field: 'command'"
        )

    timeout = int(config.get("timeout_sec", 60))
    return bash(command, timeout=timeout, agent_id="cron")


class TaskExecutor:
    _DISPATCH: dict = {
        "agent_prompt": _run_agent_prompt,
        "agent_tool": _run_agent_tool,
        "mcp_tool": _run_mcp_tool,
        "shell": _run_shell,
    }

    def execute(self, task_dict: dict, owner_id: str) -> TaskRun:
        """Dispatch and execute a scheduled task.

        Always returns a TaskRun; never raises exceptions.

        Phase 5 additions:
        - Injects vault credentials as thread-local env overrides.
        - Checks the user's LLM budget before dispatch.
        - Records accrued Anthropic cost after execution.
        """
        from orchid.budget.guard import (
            BudgetExceededError,
            BudgetGuard,
            vault_env_context,
        )

        run = TaskRun(
            task_id=task_dict.get("task_id", ""),
            owner_id=owner_id,
            task_name=task_dict.get("name", ""),
            task_type=task_dict.get("task_type", ""),
            started_at=_utcnow(),
        )

        task_type = task_dict.get("task_type", "")
        config = task_dict.get("config", {})

        dispatch_fn = self._DISPATCH.get(task_type)

        if dispatch_fn is None:
            run.finished_at = _utcnow()
            run.status = "failure"
            run.error = (
                f"Unknown task_type: {task_type!r}. Must be one of: "
                f"{sorted(self._DISPATCH)}"
            )
            return run

        guard = BudgetGuard(owner_id)

        # Reset per-task cost accumulator on this thread
        _exec_local.cost_usd = 0.0

        with vault_env_context(owner_id):
            wall_start = time.monotonic()
            try:
                guard.check()       # LLM budget
                guard.check_cpu()   # daily CPU budget (auto-resets at midnight)
                output = dispatch_fn(config, owner_id)
                run.finished_at = _utcnow()
                run.status = "success"
                run.output = output or ""
            except BudgetExceededError as exc:
                run.finished_at = _utcnow()
                run.status = "failure"
                run.error = str(exc)
            except TaskExecutionError as exc:
                run.finished_at = _utcnow()
                run.status = "failure"
                run.error = str(exc)
            except Exception as exc:
                run.finished_at = _utcnow()
                run.status = "failure"
                run.error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "Scheduled task %s raised unexpectedly", task_dict.get("task_id")
                )
            except BaseException as exc:
                # Catch SystemExit, KeyboardInterrupt, GeneratorExit etc.
                run.finished_at = _utcnow()
                run.status = "failure"
                run.error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "Scheduled task %s raised unexpected BaseException",
                    task_dict.get("task_id"),
                )
            finally:
                wall_elapsed = time.monotonic() - wall_start
                # Record LLM cost accrued during this task
                cost = getattr(_exec_local, "cost_usd", 0.0)
                if cost > 0:
                    try:
                        guard.record(cost)
                    except Exception as exc:
                        logger.warning(
                            "BudgetGuard.record failed for %s: %s", owner_id, exc
                        )
                # Record wall-clock time against CPU budget
                if wall_elapsed > 0:
                    try:
                        guard.record_cpu(wall_elapsed)
                    except Exception as exc:
                        logger.warning(
                            "BudgetGuard.record_cpu failed for %s: %s", owner_id, exc
                        )

        return run
