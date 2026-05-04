"""Tests for orchid.mcp.adapter — MCPAdapter wrapping an MCPClient."""

import sys
from unittest.mock import MagicMock

from orchid.mcp.adapter import MCPAdapter
from orchid.mcp.client import MCPClient, MCPClientError
from orchid.mcp.types import MCPResult, MCPTool


def test_adapter_connect_calls_client_connect_and_list_tools():
    """connect() delegates to client.connect() and client.list_tools()."""
    mock_client = MagicMock(spec=MCPClient)
    mock_client.list_tools.return_value = [
        MCPTool(name="echo", description="Echoes a message"),
    ]
    adapter = MCPAdapter(mock_client)
    adapter.connect()

    mock_client.connect.assert_called_once()
    mock_client.list_tools.assert_called_once()
    assert adapter._tools == [
        MCPTool(name="echo", description="Echoes a message"),
    ]


def test_adapter_list_tools_caches_and_reuses():
    """list_tools() returns cached tools without calling the client again."""
    mock_client = MagicMock(spec=MCPClient)
    mock_client.list_tools.return_value = [
        MCPTool(name="greet", description="Greets someone"),
    ]
    adapter = MCPAdapter(mock_client)
    adapter._tools = [
        MCPTool(name="greet", description="Greets someone"),
    ]

    tools1 = adapter.list_tools()
    tools2 = adapter.list_tools()

    mock_client.list_tools.assert_not_called()
    assert tools1 is tools2
    assert len(tools1) == 1
    assert tools1[0].name == "greet"


def test_adapter_call_tool_delegates_to_client():
    """call_tool() forwards name and arguments to client.call_tool()."""
    mock_client = MagicMock(spec=MCPClient)
    mock_client.call_tool.return_value = MCPResult(content="hello world")
    adapter = MCPAdapter(mock_client)

    result = adapter.call_tool("echo", {"msg": "hello"})

    mock_client.call_tool.assert_called_once_with("echo", {"msg": "hello"})
    assert result.content == "hello world"
    assert result.isError is False


def test_adapter_disconnect_clears_tools_and_calls_client():
    """disconnect() calls client.disconnect() and resets the tool cache."""
    mock_client = MagicMock(spec=MCPClient)
    mock_client.list_tools.return_value = [
        MCPTool(name="echo", description="Echoes a message"),
    ]
    adapter = MCPAdapter(mock_client)
    adapter.connect()
    assert len(adapter._tools) == 1

    adapter.disconnect()

    mock_client.disconnect.assert_called_once()
    assert adapter._tools == []