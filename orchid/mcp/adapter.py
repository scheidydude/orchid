"""MCP adapter — wraps an MCPClient with a clean, manager-facing interface."""

from __future__ import annotations

from typing import Any

from orchid.mcp.client import MCPClient, MCPClientError
from orchid.mcp.types import MCPResult, MCPTool


class MCPAdapter:
    """Adapts an ``MCPClient`` to a simple, synchronous tool interface.

    The adapter owns the client lifecycle (connect / disconnect) and
    exposes two public methods — ``list_tools()`` and ``call_tool()`` —
    that the ``MCPManager`` uses to discover and invoke tools.

    Usage::

        client = StdioMCPClient(["python", "my_server.py"])
        adapter = MCPAdapter(client)
        adapter.connect()
        tools = adapter.list_tools()
        result = adapter.call_tool("echo", {"msg": "hello"})
        adapter.disconnect()
    """

    def __init__(self, client: MCPClient) -> None:
        """Create a new adapter wrapping the given client.

        Args:
            client: An ``MCPClient`` instance (stdio, HTTP, etc.).
        """
        self._client = client
        self._tools: list[MCPTool] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to the MCP server via the underlying client."""
        self._client.connect()
        self._tools = self._client.list_tools()

    def disconnect(self) -> None:
        """Disconnect from the MCP server via the underlying client."""
        self._client.disconnect()
        self._tools = []

    # ------------------------------------------------------------------
    # Tool operations
    # ------------------------------------------------------------------

    def list_tools(self) -> list[MCPTool]:
        """Return the list of tools exposed by the MCP server.

        Returns:
            A list of ``MCPTool`` objects.

        Raises:
            MCPClientError: If the client is not connected or the call fails.
        """
        if not self._tools:
            self._tools = self._client.list_tools()
        return self._tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResult:
        """Call a tool by name with the given arguments.

        Args:
            name: The tool name to invoke.
            arguments: A dict of argument key-value pairs.

        Returns:
            ``MCPResult`` with the tool output.

        Raises:
            MCPClientError: If the tool is not found or the call fails.
        """
        return self._client.call_tool(name, arguments)