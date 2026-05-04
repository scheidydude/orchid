#!/usr/bin/env python3
"""WebSocket stream output emitter.

Sends each event as a single JSON message over a WebSocket connection.
Supports both live (async websockets) and in-memory (list) sinks.
"""

import json
from typing import Any


class WSBufferEmitter:
    """WebSocket-style emitter that collects events in memory instead of sending over a socket.

    Useful for testing or for building a batch before sending over a
    WebSocket connection.  Events are stored as raw JSON strings.
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []

    def emit(self, event: Any) -> None:
        """Append one event as a JSON string to the in-memory buffer."""
        self._buffer.append(event.to_json())

    def close(self) -> None:
        """No-op — the buffer is discarded when the emitter is garbage-collected."""
        pass

    def get_messages(self) -> list[str]:
        """Return all collected JSON message strings."""
        return list(self._buffer)

    def get_json_objects(self) -> list[Any]:
        """Return all collected events parsed back to Python objects."""
        return [json.loads(msg) for msg in self._buffer]

    def clear(self) -> None:
        """Reset the buffer."""
        self._buffer.clear()


class WSStreamEmitter:
    """Emit events as JSON messages over an async WebSocket connection.

    Parameters
    ----------
    ws:
        An ``asyncio`` WebSocket object (e.g. from the ``websockets`` library).
    close_on_close:
        Whether to close the WebSocket when ``close()`` is called.
        Set to ``False`` if the caller manages the connection lifecycle.
    """

    def __init__(
        self,
        ws: Any,
        close_on_close: bool = True,
    ) -> None:
        self._ws = ws
        self._close_on_close: bool = close_on_close
        self._closed: bool = False

    async def emit(self, event: Any) -> None:
        """Send one event as a JSON message over the WebSocket.

        Parameters
        ----------
        event:
            Any object that provides a ``to_json()`` method returning
            a JSON-serialisable string.
        """
        if self._closed:
            return
        message = event.to_json()
        await self._ws.send(message)

    async def close(self) -> None:
        """Close the WebSocket connection (if ``close_on_close`` is True)."""
        if self._closed:
            return
        self._closed = True
        if self._close_on_close:
            await self._ws.close()


class WSHandler:
    """Minimal WebSocket handler that accepts a WSStreamEmitter and dispatches events.

    Intended to be used with the ``websockets`` library:

        async def handler(websocket):
            emitter = WSStreamEmitter(websocket)
            await emitter.emit(SessionStartEvent(session_id="abc"))
            # ... send more events ...
            await emitter.close()
    """

    def __init__(self, emitter: WSStreamEmitter) -> None:
        self._emitter = emitter

    async def send_event(self, event: Any) -> None:
        """Convenience wrapper around ``emitter.emit()``."""
        await self._emitter.emit(event)

    async def close(self) -> None:
        """Convenience wrapper around ``emitter.close()``."""
        await self._emitter.close()
