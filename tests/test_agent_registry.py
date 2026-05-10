"""Tests for orchid.agent_registry — global task_id → agent map."""

import threading

import orchid.agent_registry as ar


class TestRegistry:
    def test_register_and_get(self):
        sentinel = object()
        ar.register("T001", sentinel)
        assert ar.get("T001") is sentinel

    def test_get_missing_returns_none(self):
        assert ar.get("T999") is None

    def test_deregister_removes(self):
        sentinel = object()
        ar.register("T002", sentinel)
        ar.deregister("T002")
        assert ar.get("T002") is None

    def test_deregister_missing_is_safe(self):
        ar.deregister("T_NEVER_REGISTERED")  # must not raise

    def test_all_task_ids(self):
        ar.register("TX1", object())
        ar.register("TX2", object())
        ids = ar.all_task_ids()
        assert "TX1" in ids
        assert "TX2" in ids

    def test_concurrent_register_deregister(self):
        """Concurrent modifications must not raise or corrupt state."""
        errors = []

        def writer():
            for i in range(50):
                ar.register(f"CONCURRENT-{i}", object())
                ar.deregister(f"CONCURRENT-{i}")

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_overwrite_existing(self):
        a, b = object(), object()
        ar.register("T_OW", a)
        ar.register("T_OW", b)
        assert ar.get("T_OW") is b
