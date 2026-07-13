"""Integration test for StdioMCPClient against a real subprocess MCP server."""

import sys

import pytest

from orchid.mcp.stdio_client import StdioMCPClient
from orchid.mcp.types import MCPResult, MCPTool

# Minimal MCP server: reads JSON-RPC requests from stdin, writes responses to stdout.
# Handles initialize, notifications/initialized (sends ack + proactive notification for
# _read_notification), tools/list, and tools/call.
_SERVER_SCRIPT = """
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    method = req.get("method", "")
    req_id = req.get("id")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {}}), flush=True)
    elif method == "notifications/initialized":
        pass  # JSON-RPC notification: no id, no response
    elif method == "tools/list":
        tools = [{"name": "echo", "description": "Echo a message", "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}}}]
        print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}), flush=True)
    elif method == "tools/call":
        args = req.get("params", {}).get("arguments", {})
        msg = args.get("msg", "")
        print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"text": msg}], "isError": False}}), flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_stdio_mcp_client_full_roundtrip():
    """StdioMCPClient connects to a real subprocess MCP server, lists tools, and calls echo."""
    client = StdioMCPClient(command=["python3", "-c", _SERVER_SCRIPT])
    try:
        client.connect()

        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0] == MCPTool(
            name="echo",
            description="Echo a message",
            parameters={"type": "object", "properties": {"msg": {"type": "string"}}},
        )

        result = client.call_tool("echo", {"msg": "hello"})
        assert result == MCPResult(content="hello", isError=False)
    finally:
        client.disconnect()
