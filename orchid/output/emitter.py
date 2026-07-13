from abc import ABC, abstractmethod
from typing import Any


class EmitterProtocol(ABC):
    """Abstract protocol for stream output emitters.

    Any emitter must implement ``emit()`` and ``close()``.
    The ``emit()`` method accepts any object that provides a
    ``to_json()`` method (e.g. dataclasses from ``orchid.output.events``).
    """

    @abstractmethod
    def emit(self, event: Any) -> None:
        """Emit a single event object that has a ``to_json()`` method."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the emitter and release any resources."""
        ...


class NullEmitter(EmitterProtocol):
    """No-op emitter that silently discards all events."""

    def emit(self, event: Any) -> None:
        pass

    def close(self) -> None:
        pass

# Per-project registry of live web-stream emitters. Keyed by project path.
# BackgroundRunner._run checks this to wire session events into an active
# web stream; anything registering an emitter (e.g. a web streaming route)
# should insert it here and pop it when the stream closes.
_stream_emitters: dict[str, EmitterProtocol] = {}
