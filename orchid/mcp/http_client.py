"""HTTP-based MCP client using httpx.Client (sync) for transport."""

from __future__ import annotations

from typing import Any

import httpx

from orchid.mcp.client import MCPClient, MCPClientError
from orchid.mcp.types import MCPResult, MCPTool


class HTTPMCPClient(MCPClient):
    """MCP client that communicates with a server over HTTP using JSON-RPC 2.0.

    Uses ``httpx.Client`` (synchronous) to send JSON-RPC requests to an
    HTTP endpoint.  The client performs the MCP initialisation handshake on
    ``connect()`` and keeps the ``httpx.Client`` open for the lifetime of
    the session.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> None:
        """Create a new HTTP-based MCP client.

        Args:
            url: The base URL of the MCP server (e.g. ``http://localhost:8080/mcp``).
            headers: Optional extra HTTP headers to include with every request.
            timeout: Request timeout in seconds.
        """
        self._url = url.rstrip("/")
        self._headers = headers or {}
        self._timeout = timeout
        self._client: httpx.Client | None = None
        self._next_id: int = 1
        self._tools: list[MCPTool] = []

    # ------------------------------------------------------------------
    # MCPClient ABC
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Create the HTTP connection and perform the MCP initialisation handshake."""
        self._client = httpx.Client(
            base_url=self._url,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=self._timeout,
        )
        self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "orchid", "version": "0.1.0"},
        })
        self._send_request("notifications/initialized", {})
        self._tools = self.list_tools()

    def disconnect(self) -> None:
        """Close the HTTP connection."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def list_tools(self) -> list[MCPTool]:
        """Return the list of tools exposed by the MCP server."""
        if not self._tools:
            response = self._send_request("tools/list", {})
            params = response.get("result", {}).get("tools", [])
            self._tools = [
                MCPTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    parameters=t.get("inputSchema", {}),
                )
                for t in params
            ]
        return self._tools

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
        response = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        error = response.get("error")
        if error:
            raise MCPClientError(
                error.get("message", "Tool call failed"),
                error.get("code", -1),
            )
        content_parts: list[str] = []
        for item in response.get("result", {}).get("content", []):
            if isinstance(item, dict):
                content_parts.append(item.get("text", ""))
            else:
                content_parts.append(str(item))
        return MCPResult(
            content="".join(content_parts),
            isError=response.get("result", {}).get("isError", False),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC POST request and return the response dict."""
        if self._client is None:
            raise MCPClientError("Client is not connected")
        msg_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        }
        resp = self._client.post("", json=payload)
        if resp.status_code != 200:
            raise MCPClientError(
                f"HTTP {resp.status_code}: {resp.text}",
                resp.status_code,
            )
        return resp.json()