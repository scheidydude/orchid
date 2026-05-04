"""MCP server manager — owns the lifecycle of multiple MCP server adapters."""

from __future__ import annotations

from typing import Any

from orchid.config import get
from orchid.mcp.adapter import MCPAdapter
from orchid.mcp.client import MCPClient, MCPClientError
from orchid.mcp.http_client import HTTPMCPClient
from orchid.mcp.stdio_client import StdioMCPClient
from orchid.mcp.types import MCPResult, MCPTool


class MCPManagerError(MCPClientError):
    """Raised when the MCP manager encounters a configuration or lifecycle error."""


class MCPManager:
    """Manages multiple MCP server adapters and their tool registries.

    The manager reads server definitions from the project config
    (``mcp.servers`` section), creates the appropriate client for each
    server, wraps it in an ``MCPAdapter``, and connects all of them
    during ``connect()``.

    Usage::

        manager = MCPManager()
        manager.connect()
        tools = manager.list_tools()          # all tools from all servers
        result = manager.call_tool("echo", {"msg": "hello"})
        manager.disconnect()
    """

    def __init__(self) -> None:
        """Create a new MCP manager with no servers configured."""
        self._adapters: dict[str, MCPAdapter] = {}
        self._server_config: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Server configuration
    # ------------------------------------------------------------------

    def _load_server_config(self) -> dict[str, Any]:
        """Read the MCP servers section from the project config."""
        return get("mcp.servers", {})

    def _create_client(self, name: str, config: dict[str, Any]) -> MCPClient:
        """Create an ``MCPClient`` instance from a server config dict.

        Args:
            name: The server name (used for error messages).
            config: A dict with at least a ``transport`` key (``stdio`` or ``http``).

        Returns:
            A fully constructed ``MCPClient`` subclass instance.

        Raises:
            MCPManagerError: If the transport type is unknown or required
                config keys are missing.
        """
        transport = config.get("transport", "stdio")

        if transport == "stdio":
            command = config.get("command")
            if not command:
                raise MCPManagerError(
                    f"Server '{name}' (stdio): 'command' is required in config",
                    -1,
                )
            env = config.get("env")
            if isinstance(env, str):
                # Allow env as a single string path to a .env file
                from dotenv import load_dotenv
                load_dotenv(env)
                env = None
            return StdioMCPClient(
                command=command if isinstance(command, list) else command.split(),
                env=env,
            )

        if transport == "http":
            url = config.get("url")
            if not url:
                raise MCPManagerError(
                    f"Server '{name}' (http): 'url' is required in config",
                    -1,
                )
            headers = config.get("headers")
            timeout = config.get("timeout", 30.0)
            return HTTPMCPClient(
                url=url,
                headers=headers,
                timeout=timeout,
            )

        raise MCPManagerError(
            f"Server '{name}': unknown transport '{transport}' (expected 'stdio' or 'http')",
            -1,
        )

    def discover_servers(self) -> None:
        """Load server config and create adapters (without connecting).

        This method reads the ``mcp.servers`` section from the project
        config, creates a client and adapter for each server, and stores
        them internally.  Call ``connect()`` afterwards to establish
        all connections.
        """
        self._server_config = self._load_server_config()
        self._adapters = {}

        for name, config in self._server_config.items():
            if not isinstance(config, dict):
                continue
            client = self._create_client(name, config)
            adapter = MCPAdapter(client)
            self._adapters[name] = adapter

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to every registered MCP server.

        Iterates over all discovered adapters and calls ``connect()``
        on each.  If a server fails to connect, the error is raised
        immediately — no partial state is left.

        Raises:
            MCPManagerError: If any server fails to connect.
        """
        if not self._adapters:
            self.discover_servers()

        errors: list[tuple[str, Exception]] = []
        connected: list[str] = []

        for name, adapter in self._adapters.items():
            try:
                adapter.connect()
                connected.append(name)
            except Exception as exc:
                errors.append((name, exc))

        if errors:
            # Disconnect any servers that were successfully connected
            for name in connected:
                self._adapters[name].disconnect()
            msg_parts = [f"{name}: {exc}" for name, exc in errors]
            raise MCPManagerError(
                f"Failed to connect to {len(errors)} server(s): {'; '.join(msg_parts)}",
                -1,
            )

    def disconnect(self) -> None:
        """Disconnect from every registered MCP server.

        Iterates over all adapters and calls ``disconnect()`` on each.
        Errors from individual disconnects are silently ignored.
        """
        for adapter in self._adapters.values():
            try:
                adapter.disconnect()
            except Exception:
                pass
        self._adapters = {}

    # ------------------------------------------------------------------
    # Tool operations
    # ------------------------------------------------------------------

    def list_tools(self) -> list[MCPTool]:
        """Return the combined list of tools from all connected servers.

        Returns:
            A list of ``MCPTool`` objects, one per tool across all servers.
        """
        tools: list[MCPTool] = []
        for adapter in self._adapters.values():
            tools.extend(adapter.list_tools())
        return tools

    def list_tools_by_server(self) -> dict[str, list[MCPTool]]:
        """Return tools grouped by server name.

        Returns:
            A dict mapping server name to its list of ``MCPTool`` objects.
        """
        result: dict[str, list[MCPTool]] = {}
        for name, adapter in self._adapters.items():
            result[name] = adapter.list_tools()
        return result

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResult:
        """Call a tool by name, dispatching to the correct server.

        If multiple servers expose a tool with the same name, the first
        one found (in discovery order) is used.

        Args:
            name: The tool name to invoke.
            arguments: A dict of argument key-value pairs.

        Returns:
            ``MCPResult`` with the tool output.

        Raises:
            MCPManagerError: If no server exposes the named tool.
            MCPClientError: If the tool call fails.
        """
        for name_key, adapter in self._adapters.items():
            server_tools = adapter.list_tools()
            for tool in server_tools:
                if tool.name == name:
                    return adapter.call_tool(name, arguments)
        raise MCPManagerError(
            f"No server exposes a tool named '{name}'",
            -1,
        )

    def get_adapter(self, server_name: str) -> MCPAdapter | None:
        """Return the adapter for a specific server by name.

        Args:
            server_name: The server name as defined in config.

        Returns:
            The ``MCPAdapter`` instance, or ``None`` if not found.
        """
        return self._adapters.get(server_name)
