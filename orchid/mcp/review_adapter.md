# Review: orchid/mcp/adapter.py

## Issue 1: No connection state tracking
The connect method on line 42 calls self._client.connect() but does not set a _connected flag. Similarly, disconnect on line 47 does not clear such a flag. This means there is no way for other methods to verify the client is connected before proceeding.

FAIL - line 42

## Issue 2: list_tools bypasses connection check
The list_tools method on line 55 checks if not self._tools and calls self._client.list_tools directly. If the adapter has never been connected or has been disconnected, self._tools will be empty, causing a direct client call on an unconnected client. There is no guard to ensure the client is connected first.

FAIL - line 55

## Issue 3: call_tool has no connection guard
The call_tool method on line 67 directly forwards to self._client.call_tool(name, arguments) with no check that the client is connected. A call to call_tool without a prior connect will silently proceed to the client which will likely raise an obscure error.

FAIL - line 67
