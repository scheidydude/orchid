"""Tests for orchid.mcp.http_client — HTTPMCPClient using respx to mock httpx."""

import json
import unittest

import httpx
import respx

from orchid.mcp.http_client import HTTPMCPClient
from orchid.mcp.types import MCPResult, MCPTool

_INIT_RESP = httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
_NOTIF_RESP = httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {}})


class TestHTTPMCPClient(unittest.TestCase):
    """Unit tests for HTTPMCPClient with respx mock router."""

    @respx.mock
    def test_connect_success(self):
        """connect() sends initialize, notifications/initialized, and
        tools/list requests, then caches the tool list."""
        tools_resp = httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "tools": [{"name": "echo", "description": "Echoes a message", "inputSchema": {"type": "object"}}]
            },
        })
        respx.post("").mock(side_effect=[_INIT_RESP, _NOTIF_RESP, tools_resp])

        client = HTTPMCPClient(url="http://localhost:8080/mcp")
        client.connect()

        tools = client.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertIsInstance(tools[0], MCPTool)
        self.assertEqual(tools[0].name, "echo")
        self.assertEqual(tools[0].description, "Echoes a message")

        # Verify Content-Type header was set
        request = respx.mock.calls[0].request
        self.assertEqual(request.headers.get("content-type"), "application/json")

    @respx.mock
    def test_call_tool_success(self):
        """call_tool() sends a tools/call request and returns an MCPResult
        with content assembled from the response."""
        call_resp = httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": 4,
            "result": {"content": [{"text": "hello world"}], "isError": False},
        })
        empty_tools = httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": {"tools": []}})
        respx.post("").mock(side_effect=[_INIT_RESP, _NOTIF_RESP, empty_tools, call_resp])

        client = HTTPMCPClient(url="http://localhost:8080/mcp")
        client.connect()

        result = client.call_tool("echo", {"msg": "hello"})
        self.assertIsInstance(result, MCPResult)
        self.assertEqual(result.content, "hello world")
        self.assertFalse(result.isError)

        # Verify the tools/call request body
        call_request = respx.mock.calls[-1].request
        body = json.loads(call_request.content)
        self.assertEqual(body["method"], "tools/call")
        self.assertEqual(body["params"]["name"], "echo")
        self.assertEqual(body["params"]["arguments"]["msg"], "hello")

    @respx.mock
    def test_call_tool_error(self):
        """call_tool() raises MCPClientError when the server returns an
        error field in the JSON-RPC response."""
        from orchid.mcp.client import MCPClientError

        error_resp = httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": 4,
            "error": {"message": "Tool not found", "code": -32601},
        })
        empty_tools = httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": {"tools": []}})
        respx.post("").mock(side_effect=[_INIT_RESP, _NOTIF_RESP, empty_tools, error_resp])

        client = HTTPMCPClient(url="http://localhost:8080/mcp")
        client.connect()

        with self.assertRaises(MCPClientError) as ctx:
            client.call_tool("nonexistent", {})

        self.assertIn("Tool not found", str(ctx.exception))
        self.assertEqual(ctx.exception.code, -32601)


if __name__ == "__main__":
    unittest.main()