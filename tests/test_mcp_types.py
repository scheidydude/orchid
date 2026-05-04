"""Tests for orchid.mcp.types — MCPTool, MCPResult, MCPError dataclasses."""

from orchid.mcp.types import MCPError, MCPResult, MCPTool


def test_mcp_tool_construction_and_defaults():
    """MCPTool accepts name, description and optional parameters with a default."""
    tool = MCPTool(name="echo", description="Echoes a message")
    assert tool.name == "echo"
    assert tool.description == "Echoes a message"
    assert tool.parameters == {}

    tool2 = MCPTool(
        name="greet",
        description="Greets someone",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}},
    )
    assert tool2.name == "greet"
    assert tool2.parameters["properties"]["name"]["type"] == "string"


def test_mcp_result_default_is_error():
    """MCPResult defaults isError=False and accepts a custom value."""
    ok = MCPResult(content="hello")
    assert ok.content == "hello"
    assert ok.isError is False

    err = MCPResult(content="boom", isError=True)
    assert err.content == "boom"
    assert err.isError is True


def test_mcp_error_default_code():
    """MCPError defaults code=-1 and accepts a custom code."""
    e = MCPError(message="something went wrong")
    assert e.message == "something went wrong"
    assert e.code == -1

    e2 = MCPError(message="not found", code=404)
    assert e2.message == "not found"
    assert e2.code == 404