"""HTTP-based MCP client using httpx.Client (sync) for transport.

Speaks MCP "Streamable HTTP": each JSON-RPC message is POSTed; the server
answers with either ``application/json`` or a ``text/event-stream`` body, may
issue an ``Mcp-Session-Id`` on initialize (which must be echoed on every later
request), and replies 202 with no body to notifications.
"""

from __future__ import annotations

import json
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
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout
        self._client: httpx.Client | None = None
        self._next_id: int = 1
        self._session_id: str | None = None
        self._tools: list[MCPTool] = []

    # ------------------------------------------------------------------
    # MCPClient ABC
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Create the HTTP connection and perform the MCP initialisation handshake."""
        self._client = httpx.Client(
            base_url=self._url,
            headers={
                "Accept": "application/json, text/event-stream",
                **self._headers,
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )
        response = self._send_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "orchid", "version": "0.1.0"},
        })
        if response.get("error"):
            self.disconnect()
            raise MCPClientError(
                f"Initialisation failed: {response['error'].get('message')}",
                response["error"].get("code", -1),
            )
        self._send_request("notifications/initialized", {}, notify=True)
        self._tools = self.list_tools()

    def disconnect(self) -> None:
        """Close the HTTP connection (ending the server session, if one was issued)."""
        if self._client is not None:
            if self._session_id:
                try:
                    self._client.delete("", headers={"Mcp-Session-Id": self._session_id})
                except httpx.HTTPError:
                    pass  # best effort; session will expire server-side
            self._client.close()
            self._client = None
            self._session_id = None

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

    def _send_request(
        self, method: str, params: dict[str, Any] | None = None, notify: bool = False
    ) -> dict[str, Any]:
        """POST a JSON-RPC message and return the matching response dict.

        With ``notify=True`` the message is sent without an ``id`` (a JSON-RPC
        notification) and an empty dict is returned.
        """
        if self._client is None:
            raise MCPClientError("Client is not connected")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        msg_id: int | None = None
        if not notify:
            msg_id = self._next_id
            self._next_id += 1
            payload["id"] = msg_id
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else None
        resp = self._client.post("", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise MCPClientError(
                f"HTTP {resp.status_code}: {resp.text}",
                resp.status_code,
            )
        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if notify or resp.status_code == 202 or not resp.content:
            return {}
        if "text/event-stream" in resp.headers.get("content-type", ""):
            return self._parse_sse(resp.text, msg_id)
        return resp.json()

    @staticmethod
    def _parse_sse(body: str, msg_id: int | None) -> dict[str, Any]:
        """Return the JSON-RPC response with ``id == msg_id`` from an SSE body.

        Other events (server notifications, requests) are ignored.
        """
        for event in body.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(
                line[5:].lstrip() for line in event.split("\n") if line.startswith("data:")
            )
            if not data:
                continue
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == msg_id and ("result" in msg or "error" in msg):
                return msg
        raise MCPClientError("No JSON-RPC response found in event stream", -1)
