# Scheduler task executor
from __future__ import annotations

import logging
from datetime import UTC, datetime

from orchid.cron.types import TaskRun, _utcnow

logger = logging.getLogger(__name__)


class TaskExecutionError(Exception):
    """Raised for known, non-retriable config errors."""


def _run_agent_prompt(config: dict) -> str:
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
            from orchid.mcp.manager import MCPManager

            mgr = MCPManager()
            mgr.discover_servers()
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


def _run_mcp_tool(config: dict) -> str:
    """Execute an mcp_tool type task."""
    from orchid.mcp.manager import MCPManager

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

    mgr = MCPManager()
    mgr.discover_servers()
    adapter = mgr.get_adapter(server_name)
    if adapter is None:
        raise TaskExecutionError(
            f"MCP server '{server_name}' not found in config"
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


def _run_shell(config: dict) -> str:
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
        "mcp_tool": _run_mcp_tool,
        "shell": _run_shell,
    }

    def execute(self, task_dict: dict, owner_id: str) -> TaskRun:
        """Dispatch and execute a scheduled task.

        Always returns a TaskRun; never raises exceptions.
        """
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

        try:
            output = dispatch_fn(config)
            run.finished_at = _utcnow()
            run.status = "success"
            run.output = output or ""
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
                "Scheduled task %s raised unexpected BaseException", task_dict.get("task_id")
            )

        return run
