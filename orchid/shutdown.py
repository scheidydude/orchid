"""Process-wide graceful shutdown coordination.

Import this module anywhere to check or signal shutdown without
creating circular imports between runner, orchestrator, and agents.
"""

from __future__ import annotations

import threading

_shutdown_event = threading.Event()


def request_shutdown() -> None:
    """Signal all agents and runners to stop at their next iteration boundary."""
    _shutdown_event.set()


def is_shutting_down() -> bool:
    return _shutdown_event.is_set()


def clear() -> None:
    """Reset shutdown state — for testing only."""
    _shutdown_event.clear()
