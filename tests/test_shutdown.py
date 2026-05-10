"""Tests for orchid.shutdown — process-wide graceful shutdown event."""

import threading
import time

import pytest

from orchid.shutdown import clear, is_shutting_down, request_shutdown


class TestShutdownModule:
    def test_initially_not_shutting_down(self):
        assert is_shutting_down() is False

    def test_request_shutdown_sets_flag(self):
        request_shutdown()
        assert is_shutting_down() is True

    def test_clear_resets_flag(self):
        request_shutdown()
        clear()
        assert is_shutting_down() is False

    def test_idempotent_request(self):
        request_shutdown()
        request_shutdown()
        assert is_shutting_down() is True

    def test_thread_sees_shutdown(self):
        """Thread started before request_shutdown() must see the event."""
        results = []

        def worker():
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if is_shutting_down():
                    results.append("saw_shutdown")
                    return
                time.sleep(0.01)
            results.append("timed_out")

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.05)
        request_shutdown()
        t.join(timeout=2.0)
        assert results == ["saw_shutdown"]


class TestAgentCancelOnShutdown:
    def test_cancel_event_raises_on_shutdown(self):
        """BaseAgent.run() raises AgentCancelledError when shutdown is set."""
        from orchid.agents.base import AgentCancelledError, BaseAgent
        from unittest.mock import patch

        request_shutdown()
        agent = BaseAgent()

        # patch where call is used (already imported into base module)
        with patch("orchid.agents.base.call", return_value="Thought: x\nFinal Answer: y"):
            with pytest.raises(AgentCancelledError, match="shutdown"):
                agent.run("task")

    def test_cancel_saves_checkpoint_on_shutdown(self, tmp_path):
        """Checkpoint is written before raising on shutdown."""
        from orchid.agents.base import AgentCancelledError, BaseAgent
        from orchid.checkpoint.store import CheckpointStore
        from unittest.mock import patch

        store = CheckpointStore(tmp_path)
        agent = BaseAgent()
        agent.set_checkpoint_store(store)
        agent._current_task_id = "T001"

        request_shutdown()
        with patch("orchid.agents.base.call", return_value="Thought: x\nFinal Answer: y"):
            with pytest.raises(AgentCancelledError):
                agent.run("task")

        cp = store.load_react_checkpoint("T001")
        assert cp is not None
        assert cp.task_id == "T001"
