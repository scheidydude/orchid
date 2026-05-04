"""Hook registry for Orchid V2.

Manages registration and dispatch of hooks to event handlers.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from orchid.hooks.events import HookEvent

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    """Aggregated result from firing all handlers for an event."""
    blocked: bool = False
    mutated_context: dict[str, Any] | None = None
    error: str | None = None
    results: list[Any] = field(default_factory=list)


class HookRegistry:
    """Central registry for hook event handlers.

    Supports:
    - Registration of handlers for specific event types
    - Priority-based execution order
    - Sync/async execution modes
    - Error handling without crashing the main loop
    """

    _instance: HookRegistry | None = None

    def __new__(cls) -> HookRegistry:
        """Singleton pattern for global hook registry."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)
        self._registered: list[str] = []

    def register(
        self,
        event_type: str,
        handler: Callable[[HookEvent], None] | Callable[[HookEvent], Any],
        priority: int = 0,
        mode: str = "sync",
        timeout: int = 30,
    ) -> str:
        """Register a handler for an event type.

        Args:
            event_type: The event type to listen for
            handler: Callable that receives HookEvent
            priority: Higher numbers run first (default 0)
            mode: "sync", "async", or "background"
            timeout: Timeout in seconds for sync handlers

        Returns:
            Handler ID for potential unregistration
        """
        import uuid

        handler_id = str(uuid.uuid4())
        hook_handler = HookHandler(
            id=handler_id,
            event_type=event_type,
            handler=handler,
            priority=priority,
            mode=mode,
            timeout=timeout,
        )

        self._handlers[event_type].append(hook_handler)
        # Sort by priority (descending)
        self._handlers[event_type].sort(key=lambda h: h.priority, reverse=True)
        self._registered.append(handler_id)

        logger.debug("Registered hook handler %s for event %s", handler_id, event_type)
        return handler_id

    def unregister(self, handler_id: str) -> bool:
        """Unregister a handler by ID."""
        for event_type, handlers in self._handlers.items():
            for i, h in enumerate(handlers):
                if h.id == handler_id:
                    handlers.pop(i)
                    self._registered.remove(handler_id)
                    logger.debug("Unregistered hook handler %s", handler_id)
                    return True
        return False

    def fire(self, event: HookEvent, ignore_errors: bool = True) -> HookResult:
        """Fire all handlers for an event type.

        Args:
            event: The event to fire
            ignore_errors: If True, catch and log errors without raising

        Returns:
            HookResult with blocked flag, mutated_context, error, and raw results list
        """
        handlers = self._handlers.get(event.event_type, [])
        hook_result = HookResult()

        if not handlers:
            return hook_result

        logger.debug("Firing %d handlers for event %s", len(handlers), event.event_type)

        for handler in handlers:
            try:
                if handler.mode == "background":
                    import threading
                    thread = threading.Thread(
                        target=self._execute_handler,
                        args=(handler, event),
                        daemon=True,
                    )
                    thread.start()
                elif handler.mode == "async":
                    import threading
                    thread = threading.Thread(
                        target=self._execute_handler_with_timeout,
                        args=(handler, event, handler.timeout),
                        daemon=True,
                    )
                    thread.start()
                else:  # sync
                    result = self._execute_handler_with_timeout(
                        handler, event, handler.timeout
                    )
                    hook_result.results.append(result)
                    # Check if handler signalled blocking or returned mutations
                    if isinstance(result, dict):
                        if result.get("blocked"):
                            hook_result.blocked = True
                            if not hook_result.error:
                                hook_result.error = result.get("error") or result.get("reason") or "blocked by hook"
                        if "mutated_context" in result and result["mutated_context"]:
                            hook_result.mutated_context = result["mutated_context"]

            except Exception as e:
                if ignore_errors:
                    logger.error(
                        "Hook handler %s failed for event %s: %s",
                        handler.id, event.event_type, e
                    )
                else:
                    raise

        return hook_result

    def _execute_handler(self, handler: "HookHandler", event: HookEvent) -> Any:
        """Execute a handler synchronously."""
        return handler.handler(event)

    def _execute_handler_with_timeout(
        self, handler: "HookHandler", event: HookEvent, timeout: int
    ) -> Any:
        """Execute a handler with timeout."""
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._execute_handler, handler, event)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.error(
                    "Hook handler %s timed out after %ds for event %s",
                    handler.id, timeout, event.event_type
                )
                raise

    def get_handlers_for_event(self, event_type: str) -> list["HookHandler"]:
        """Get all handlers registered for an event type."""
        return self._handlers.get(event_type, [])

    def clear(self) -> None:
        """Clear all registered handlers."""
        self._handlers.clear()
        self._registered.clear()
        logger.info("Cleared all hook handlers")

    @property
    def registered_count(self) -> int:
        """Total number of registered handlers."""
        return sum(len(h) for h in self._handlers.values())


class HookHandler:
    """Represents a registered hook handler."""

    def __init__(
        self,
        id: str,
        event_type: str,
        handler: Callable,
        priority: int = 0,
        mode: str = "sync",
        timeout: int = 30,
    ):
        self.id = id
        self.event_type = event_type
        self.handler = handler
        self.priority = priority
        self.mode = mode
        self.timeout = timeout

    def __repr__(self) -> str:
        return (
            f"HookHandler(id={self.id}, event={self.event_type}, "
            f"mode={self.mode}, priority={self.priority})"
        )