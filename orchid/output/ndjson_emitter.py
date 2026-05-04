#!/usr/bin/env python3
"""NDJSON stream output emitter.

Writes each event as a single JSON line terminated by ``\\n``.
Supports both file-based and in-memory (list) sinks.
"""

import json
import sys
from typing import Any, List, Optional, TextIO

from orchid.output.emitter import EmitterProtocol


class NDJSONEmitter(EmitterProtocol):
    """Emit events as newline-delimited JSON (NDJSON).

    Parameters
    ----------
    out:
        A writable text stream.  Defaults to ``sys.stdout``.
    flush:
        Whether to flush the stream after every ``emit()`` call.
        Useful when the stream is line-buffered or when writing to
        a pipe / WebSocket that expects immediate delivery.
    """

    def __init__(
        self,
        out: Optional[TextIO] = None,
        flush: bool = True,
    ) -> None:
        self._out: TextIO = out or sys.stdout
        self._flush: bool = flush

    def emit(self, event: Any) -> None:
        """Write one event as a single NDJSON line.

        Parameters
        ----------
        event:
            Any object that provides a ``to_json()`` method returning
            a JSON-serialisable string.
        """
        json_line = event.to_json()
        self._out.write(json_line + "\n")
        if self._flush:
            self._out.flush()

    def close(self) -> None:
        """Close the underlying stream (if it is not ``sys.stdout``)."""
        if self._out is not sys.stdout:
            self._out.close()


class NDJSONBufferEmitter(EmitterProtocol):
    """NDJSON emitter that collects events in memory instead of writing to a stream.

    Useful for testing or for building a batch before sending over a
    WebSocket / HTTP response.
    """

    def __init__(self) -> None:
        self._buffer: List[str] = []

    def emit(self, event: Any) -> None:
        """Append one NDJSON line to the in-memory buffer."""
        self._buffer.append(event.to_json())

    def close(self) -> None:
        """No-op — the buffer is discarded when the emitter is garbage-collected."""
        pass

    def get_lines(self) -> List[str]:
        """Return all collected NDJSON lines."""
        return list(self._buffer)

    def get_json_objects(self) -> List[Any]:
        """Return all collected events parsed back to Python objects."""
        return [json.loads(line) for line in self._buffer]

    def clear(self) -> None:
        """Reset the buffer."""
        self._buffer.clear()