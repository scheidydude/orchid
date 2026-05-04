"""Stdio-based MCP client using subprocess.Popen for transport."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from orchid.mcp.client import MCPClient, MCPClientError
from orchid.mcp.types import MCPResult, MCPTool


class StdioMCPClient(MCPClient):
    """MCP client that communicates with a server via a subprocess's stdin/stdout.

    Uses JSON-RPC 2.0 over the subprocess pipes. The client starts the server
    process on ``connect()`` and tears it down on ``disconnect()``.
    """

    def __init__(self, command: list[str], env: dict[str, str] | None = None) -> None:
        """Create a new stdio-based MCP client.

        Args:
            command: The shell command (as a list) to start the MCP server.
            env: Optional environment variables passed to the subprocess.
        """
        self._command = command
        self._env = env
        self._process: subprocess.Popen[bytes] | None = None
        self._next_id: int = 1
        self._tools: list[MCPTool] = []

    # ------------------------------------------------------------------
    # MCPClient ABC
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Start the subprocess and perform the MCP initialisation handshake."""
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "orchid", "version": "0.1.0"},
        })
        response = self._read_response()
        if response.get("error"):
            self.disconnect()
            raise MCPClientError(
                f"Initialisation failed: {response['error'].get('message')}",
                response["error"].get("code", -1),
            )
        self._send_request("notifications/initialized", {})
        self._read_notification()
        self._tools = self.list_tools()

    def disconnect(self) -> None:
        """Terminate the subprocess if it is still running."""
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

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
        """Send a JSON-RPC request and return the response dict."""
        msg_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        }
        self._write(json.dumps(payload) + "\n")
        return self._read_response()

    def _write(self, data: str) -> None:
        """Write a line to the subprocess stdin."""
        if self._process is None or self._process.stdin is None:
            raise MCPClientError("Process is not running")
        self._process.stdin.write(data)
        self._process.stdin.flush()

    def _read_response(self) -> dict[str, Any]:
        """Read a single JSON-RPC response (or error) from stdout."""
        if self._process is None or self._process.stdout is None:
            raise MCPClientError("Process is not running")
        line = self._process.stdout.readline()
        if not line:
            raise MCPClientError("Unexpected end of stdout")
        return json.loads(line)

    def _read_notification(self) -> dict[str, Any]:
        """Read a JSON-RPC notification (no id) from stdout."""
        if self._process is None or self._process.stdout is None:
            raise MCPClientError("Process is not running")
        line = self._process.stdout.readline()
        if not line:
            raise MCPClientError("Unexpected end of stdout")
        return json.loads(line)
