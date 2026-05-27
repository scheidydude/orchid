# Scheduler task executor
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from orchid.cron.types import TaskRun, _utcnow

logger = logging.getLogger(__name__)

_AGENT_TOOL_MAX_ITERS = 10
_AGENT_TOOL_MODEL = "claude-sonnet-4-6"
_AGENT_TOOL_MAX_TOKENS = 4096


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


def _run_agent_tool(config: dict) -> str:
    """Execute an agent_tool task: agentic loop with multiple MCP servers.

    Config fields:
        servers       list[str]  Required. MCP server names from config.
        prompt        str        Required. Initial user message.
        system        str        Optional. System prompt override.
        model         str        Optional. Claude model ID.
        max_tokens    int        Optional. Max tokens per API call.
        max_iterations int       Optional. Loop iteration cap (default 10).

    Tool names are prefixed ``server__toolname`` to avoid cross-server
    collisions.  The prefix is stripped before dispatching to the adapter.
    """
    import anthropic as anthropic_sdk

    from orchid.mcp.manager import MCPManager

    servers = config.get("servers", [])
    if not servers:
        raise TaskExecutionError(
            "agent_tool config missing required field: 'servers'"
        )

    prompt = config.get("prompt", "").strip()
    if not prompt:
        raise TaskExecutionError(
            "agent_tool config missing required field: 'prompt'"
        )

    max_iters = int(config.get("max_iterations", _AGENT_TOOL_MAX_ITERS))
    model = config.get("model", _AGENT_TOOL_MODEL)
    max_tokens = int(config.get("max_tokens", _AGENT_TOOL_MAX_TOKENS))
    system = config.get("system", "You are a helpful assistant.")

    # --- Connect all requested servers -----------------------------------
    mgr = MCPManager()
    mgr.discover_servers()

    adapters: dict[str, object] = {}
    tool_defs: list[dict] = []
    # prefixed_name → (adapter, original_tool_name)
    tool_map: dict[str, tuple] = {}

    try:
        for server_name in servers:
            adapter = mgr.get_adapter(server_name)
            if adapter is None:
                raise TaskExecutionError(
                    f"MCP server '{server_name}' not found in config"
                )
            adapter.connect()
            adapters[server_name] = adapter
            for tool in adapter.list_tools():
                prefixed = f"{server_name}__{tool.name}"
                tool_defs.append(
                    {
                        "name": prefixed,
                        "description": tool.description,
                        "input_schema": tool.parameters
                        or {"type": "object", "properties": {}},
                    }
                )
                tool_map[prefixed] = (adapter, tool.name)

        # --- Agentic tool-use loop ---------------------------------------
        client = anthropic_sdk.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY")
        )
        messages: list[dict] = [{"role": "user", "content": prompt}]

        for iteration in range(max_iters):
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=tool_defs,
                messages=list(messages),  # snapshot; safe against post-call mutation
            )

            # Serialise assistant turn
            assistant_content: list[dict] = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append(
                        {"type": "text", "text": block.text}
                    )
                elif block.type == "tool_use":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
            messages.append({"role": "assistant", "content": assistant_content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        return block.text
                return ""

            if response.stop_reason != "tool_use":
                break

            # Dispatch tool calls → collect results
            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name not in tool_map:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: unknown tool '{block.name}'",
                            "is_error": True,
                        }
                    )
                    continue
                adapter, original_name = tool_map[block.name]
                try:
                    result = adapter.call_tool(original_name, block.input or {})
                    content = result.content
                    if isinstance(content, list):
                        parts: list[str] = []
                        for item in content:
                            if isinstance(item, dict):
                                parts.append(str(item.get("text", str(item))))
                            else:
                                parts.append(str(item))
                        content = "\n".join(parts)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(content),
                            "is_error": result.isError,
                        }
                    )
                except Exception as exc:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: {exc}",
                            "is_error": True,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        # Max iterations reached — return last text block found
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block["text"]
        return f"[agent_tool: reached max_iterations={max_iters} without end_turn]"

    finally:
        for adapter in adapters.values():
            try:
                adapter.disconnect()
            except Exception:
                pass


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
        "agent_tool": _run_agent_tool,
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
