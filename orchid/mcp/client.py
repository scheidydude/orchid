"""MCP client ABC — abstract base for all MCP transport implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from orchid.mcp.types import MCPResult, MCPTool


class MCPClientError(Exception):
    """Raised when an MCP client encounters a transport or protocol error."""

    def __init__(self, message: str, code: int = -1) -> None:
        self.code = code
        super().__init__(message)


class MCPClient(ABC):
    """Abstract base class for MCP (Model Context Protocol) transport clients.

    Subclasses implement the transport layer (stdio, HTTP, etc.) and provide
    a unified interface for listing tools and calling them.
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish the underlying transport connection."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the underlying transport connection."""

    @abstractmethod
    def list_tools(self) -> list[MCPTool]:
        """Return the list of tools exposed by the MCP server."""

    @abstractmethod
    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResult:
        """Call a tool by name with the given arguments.

        Args:
            name: The tool name to invoke.
            arguments: A dict of argument key-value pairs.

        Returns:
            MCPResult with the tool output.

        Raises:
            MCPClientError: If the tool is not found or the call fails.
        """
