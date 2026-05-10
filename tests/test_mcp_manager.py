"""Tests for orchid.mcp.manager — MCPManager owning multiple MCP server adapters."""

from unittest.mock import MagicMock, patch

import pytest

from orchid.mcp.adapter import MCPAdapter
from orchid.mcp.manager import MCPManager, MCPManagerError
from orchid.mcp.types import MCPResult, MCPTool


@patch("orchid.mcp.manager.get")
def test_discover_servers_creates_adapters_from_config(mock_get):
    """discover_servers() reads config and creates one adapter per server."""
    mock_get.return_value = {
        "server_a": {"transport": "stdio", "command": ["python", "server.py"]},
        "server_b": {"transport": "http", "url": "http://localhost:8080"},
    }
    manager = MCPManager()
    manager.discover_servers()

    assert len(manager._adapters) == 2
    assert "server_a" in manager._adapters
    assert "server_b" in manager._adapters
    assert isinstance(manager._adapters["server_a"], MCPAdapter)
    assert isinstance(manager._adapters["server_b"], MCPAdapter)


@patch("orchid.mcp.manager.get")
@patch("orchid.mcp.manager.MCPAdapter")
def test_connect_raises_on_server_failure(mock_adapter_cls, mock_get):
    """connect() raises MCPManagerError if any server fails to connect."""
    mock_get.return_value = {
        "server_a": {"transport": "stdio", "command": ["python", "a.py"]},
        "server_b": {"transport": "stdio", "command": ["python", "b.py"]},
    }
    manager = MCPManager()
    manager.discover_servers()

    # Replace the real adapters with mocks so we control connect()
    adapter_a_mock = MagicMock(spec=MCPAdapter)
    adapter_b_mock = MagicMock(spec=MCPAdapter)
    adapter_b_mock.connect = MagicMock(side_effect=RuntimeError("connection refused"))
    manager._adapters = {"server_a": adapter_a_mock, "server_b": adapter_b_mock}

    with pytest.raises(MCPManagerError) as exc_info:
        manager.connect()

    assert "Failed to connect to 1 server" in str(exc_info.value)
    # Verify that server_a was connected then rolled back
    adapter_a_mock.disconnect.assert_called_once()


def test_call_tool_dispatches_to_correct_server():
    """call_tool() finds the first adapter exposing the tool name."""
    manager = MCPManager()
    adapter_a = MagicMock(spec=MCPAdapter)
    adapter_a.list_tools.return_value = [
        MCPTool(name="echo", description="Echoes"),
    ]
    adapter_a.call_tool.return_value = MCPResult(content="echoed")
    adapter_b = MagicMock(spec=MCPAdapter)
    adapter_b.list_tools.return_value = [
        MCPTool(name="greet", description="Greets"),
    ]
    adapter_b.call_tool.return_value = MCPResult(content="greeted")
    manager._adapters = {"a": adapter_a, "b": adapter_b}

    result = manager.call_tool("echo", {"msg": "hi"})

    adapter_a.call_tool.assert_called_once_with("echo", {"msg": "hi"})
    assert result.content == "echoed"