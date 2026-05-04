"""Tests for orchid.mcp.stdio_client — StdioMCPClient using unittest.mock.patch."""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from orchid.mcp.stdio_client import StdioMCPClient
from orchid.mcp.types import MCPResult, MCPTool


class TestStdioMCPClient(unittest.TestCase):
    """Unit tests for StdioMCPClient with mocked subprocess."""

    @patch("orchid.mcp.stdio_client.subprocess.Popen")
    def test_connect_success(self, mock_popen):
        """connect() starts the subprocess, sends initialize, reads response,
        calls list_tools, and stores the tool list."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_popen.return_value = mock_proc

        # connect() calls _read_response() TWICE for initialize (lines 46+51),
        # then _send_request for notifications/initialized (line 58),
        # then _read_notification (line 59), then list_tools (line 60).
        # Total = 5 readline calls during connect().
        mock_proc.stdout.readline.side_effect = [
            # 1st: initialize response (from _send_request in _read_response)
            '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}',
            # 2nd: initialize response (from the extra _read_response call on line 51)
            '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}',
            # 3rd: notifications/initialized response
            '{"jsonrpc": "2.0", "id": 2}',
            # 4th: notification (no id)
            '{"jsonrpc": "2.0", "method": "notifications/initialized"}',
            # 5th: tools/list response
            '{"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "echo", "description": "Echoes a message", "inputSchema": {"type": "object"}}]}}',
        ]

        client = StdioMCPClient(command=["python", "server.py"])
        client.connect()

        mock_popen.assert_called_once_with(
            ["python", "server.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Verify stdin received the initialize request
        calls = mock_proc.stdin.write.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn('"method": "initialize"', calls[0][0][0])
        self.assertIn('"method": "notifications/initialized"', calls[1][0][0])

        # list_tools should return the cached tools
        tools = client.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "echo")

    @patch("orchid.mcp.stdio_client.subprocess.Popen")
    def test_connect_initialization_failure(self, mock_popen):
        """connect() raises MCPClientError when the initialize response
        contains an error field."""
        from orchid.mcp.client import MCPClientError

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_popen.return_value = mock_proc

        # connect() calls _read_response() twice for initialize:
        # 1st from _send_request, 2nd from the extra call on line 51.
        mock_proc.stdout.readline.side_effect = [
            '{"jsonrpc": "2.0", "id": 1, "error": {"message": "bad protocol", "code": -32600}}',
            '{"jsonrpc": "2.0", "id": 1, "error": {"message": "bad protocol", "code": -32600}}',
        ]

        client = StdioMCPClient(command=["python", "server.py"])
        with self.assertRaises(MCPClientError) as ctx:
            client.connect()

        self.assertIn("bad protocol", str(ctx.exception))
        mock_proc.terminate.assert_called_once()

    @patch("orchid.mcp.stdio_client.subprocess.Popen")
    def test_disconnect_terminates_process(self, mock_popen):
        """disconnect() terminates the subprocess and waits for it."""
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        client = StdioMCPClient(command=["python", "server.py"])
        client._process = mock_proc
        client.disconnect()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        self.assertIsNone(client._process)

    @patch("orchid.mcp.stdio_client.subprocess.Popen")
    def test_call_tool_success(self, mock_popen):
        """call_tool() sends a tools/call request and returns MCPResult
        with content assembled from the response."""
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_popen.return_value = mock_proc

        # connect() needs 5 readline calls, then call_tool needs 1 more
        mock_proc.stdout.readline.side_effect = [
            # 1st: initialize response (from _send_request)
            '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}',
            # 2nd: initialize response (from extra _read_response on line 51)
            '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}',
            # 3rd: notifications/initialized response
            '{"jsonrpc": "2.0", "id": 2}',
            # 4th: notification (no id)
            '{"jsonrpc": "2.0", "method": "notifications/initialized"}',
            # 5th: tools/list response (empty)
            '{"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}',
            # 6th: tools/call response
            '{"jsonrpc": "2.0", "id": 4, "result": {"content": [{"text": "hello world"}], "isError": false}}',
        ]

        client = StdioMCPClient(command=["python", "server.py"])
        client.connect()

        result = client.call_tool("echo", {"msg": "hello"})
        self.assertIsInstance(result, MCPResult)
        self.assertEqual(result.content, "hello world")
        self.assertFalse(result.isError)

        # Verify the tools/call request was written to stdin
        calls = mock_proc.stdin.write.call_args_list
        tools_call = calls[-1][0][0]
        self.assertIn('"method": "tools/call"', tools_call)
        self.assertIn('"name": "echo"', tools_call)


if __name__ == "__main__":
    unittest.main()
