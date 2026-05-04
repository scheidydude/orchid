"""Tests for HookRegistry and HookResult (T102)."""

import threading
import time

import pytest

from orchid.hooks.events import HookEvent, TASK_START, TASK_COMPLETE
from orchid.hooks.registry import HookHandler, HookRegistry, HookResult


def _clear_registry():
    HookRegistry().clear()


class TestHookResult:
    def test_default_not_blocked(self):
        result = HookResult()
        assert result.blocked is False
        assert result.error is None
        assert result.mutated_context is None
        assert result.results == []

    def test_blocked_result(self):
        result = HookResult(blocked=True, error="hook said no")
        assert result.blocked is True
        assert result.error == "hook said no"

    def test_mutated_context(self):
        result = HookResult(mutated_context={"key": "value"})
        assert result.mutated_context == {"key": "value"}


class TestHookRegistry:
    def setup_method(self):
        self.registry = HookRegistry()
        self.registry.clear()

    def teardown_method(self):
        self.registry.clear()

    def test_singleton(self):
        assert HookRegistry() is HookRegistry()

    def test_register_returns_id(self):
        hid = self.registry.register("test_event", lambda e: None)
        assert hid is not None
        assert len(self.registry._registered) == 1

    def test_register_multiple_priority_order(self):
        self.registry.register("test_event", lambda e: "a", priority=10)
        self.registry.register("test_event", lambda e: "b", priority=20)
        self.registry.register("test_event", lambda e: "c", priority=15)
        handlers = self.registry._handlers["test_event"]
        assert [h.priority for h in handlers] == [20, 15, 10]

    def test_unregister(self):
        hid = self.registry.register("test_event", lambda e: None)
        assert self.registry.unregister(hid) is True
        assert len(self.registry._registered) == 0

    def test_unregister_nonexistent(self):
        assert self.registry.unregister("no-such-id") is False

    def test_fire_returns_hook_result(self):
        self.registry.register("test_event", lambda e: "x")
        event = HookEvent(event_type="test_event")
        result = self.registry.fire(event)
        assert isinstance(result, HookResult)
        assert result.results == ["x"]

    def test_fire_sync_priority_order(self):
        order = []
        self.registry.register("test_event", lambda e: order.append("a") or "a", priority=10)
        self.registry.register("test_event", lambda e: order.append("b") or "b", priority=20)
        event = HookEvent(event_type="test_event")
        result = self.registry.fire(event)
        assert order == ["b", "a"]
        assert result.results == ["b", "a"]

    def test_fire_no_handlers_returns_empty_result(self):
        event = HookEvent(event_type="no_handlers_event")
        result = self.registry.fire(event)
        assert isinstance(result, HookResult)
        assert result.results == []
        assert result.blocked is False

    def test_blocking_handler_sets_blocked(self):
        def blocking_handler(event):
            return {"blocked": True, "error": "not allowed"}

        self.registry.register("test_event", blocking_handler, mode="sync")
        event = HookEvent(event_type="test_event")
        result = self.registry.fire(event)
        assert result.blocked is True
        assert result.error == "not allowed"

    def test_blocking_handler_halts_with_reason(self):
        self.registry.register(
            "test_event",
            lambda e: {"blocked": True, "reason": "security policy"},
            mode="sync",
        )
        event = HookEvent(event_type="test_event")
        result = self.registry.fire(event)
        assert result.blocked is True
        assert "security policy" in (result.error or "")

    def test_mutated_context_propagated(self):
        self.registry.register(
            "test_event",
            lambda e: {"mutated_context": {"extra": "data"}},
            mode="sync",
        )
        event = HookEvent(event_type="test_event")
        result = self.registry.fire(event)
        assert result.mutated_context == {"extra": "data"}

    def test_non_blocking_handler_does_not_block(self):
        results = []
        self.registry.register(
            "test_event",
            lambda e: results.append("ran"),
            mode="background",
        )
        event = HookEvent(event_type="test_event")
        hook_result = self.registry.fire(event)
        assert hook_result.blocked is False
        time.sleep(0.05)
        assert "ran" in results

    def test_async_handler_does_not_block_caller(self):
        results = []
        lock = threading.Lock()

        def slow_handler(event):
            time.sleep(0.05)
            with lock:
                results.append("done")

        self.registry.register("test_event", slow_handler, mode="async")
        event = HookEvent(event_type="test_event")
        hook_result = self.registry.fire(event)
        # Fire returns immediately; blocked must be False
        assert hook_result.blocked is False

    def test_exception_in_handler_does_not_propagate(self):
        def bad_handler(event):
            raise RuntimeError("boom")

        def good_handler(event):
            return "ok"

        self.registry.register("test_event", bad_handler, priority=20)
        self.registry.register("test_event", good_handler, priority=10)
        event = HookEvent(event_type="test_event")
        result = self.registry.fire(event, ignore_errors=True)
        assert "ok" in result.results

    def test_exception_propagates_when_ignore_false(self):
        def bad_handler(event):
            raise RuntimeError("boom")

        self.registry.register("test_event", bad_handler)
        event = HookEvent(event_type="test_event")
        with pytest.raises(RuntimeError, match="boom"):
            self.registry.fire(event, ignore_errors=False)

    def test_clear(self):
        self.registry.register("e1", lambda e: None)
        self.registry.register("e2", lambda e: None)
        self.registry.clear()
        assert self.registry.registered_count == 0

    def test_registered_count(self):
        self.registry.register("e1", lambda e: None)
        self.registry.register("e1", lambda e: None)
        self.registry.register("e2", lambda e: None)
        assert self.registry.registered_count == 3


class TestHookHandler:
    def test_create(self):
        h = HookHandler(
            id="abc",
            event_type="test_event",
            handler=lambda e: None,
            priority=5,
            mode="sync",
            timeout=10,
        )
        assert h.id == "abc"
        assert h.priority == 5
        assert h.mode == "sync"

    def test_repr(self):
        h = HookHandler(id="abc", event_type="test_event", handler=lambda e: None)
        assert "abc" in repr(h)
        assert "test_event" in repr(h)
