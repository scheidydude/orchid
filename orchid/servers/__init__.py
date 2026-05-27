"""Orchid built-in MCP servers.

Each module in this package is a standalone JSON-RPC 2.0 MCP server
that can be launched as a subprocess by MCPManager.  They ship with the
orchid package so users do not need separate npm/pip installs.

Entry points (registered in pyproject.toml):
    orchid-mcp-smtp   — SMTP email sender (orchid.servers.smtp:main)
"""
