"""Tests for mid-run context injection."""

from __future__ import annotations

from unittest.mock import patch


def test_injection_queue_file_created(tmp_path):
    """BackgroundRunner.inject() creates the queue file."""
    from orchid.interfaces.background_runner import BackgroundRunner

    runner = BackgroundRunner(str(tmp_path))
    runner.inject("use stdlib not third party")

    queue = tmp_path / ".orchid" / "inject.queue"
    assert queue.exists()
    assert "use stdlib not third party" in queue.read_text(encoding="utf-8")


def test_injection_appends_multiple_lines(tmp_path):
    """Multiple inject() calls accumulate in the queue."""
    from orchid.interfaces.background_runner import BackgroundRunner

    runner = BackgroundRunner(str(tmp_path))
    runner.inject("first injection")
    runner.inject("second injection")

    queue = tmp_path / ".orchid" / "inject.queue"
    content = queue.read_text(encoding="utf-8")
    assert "first injection" in content
    assert "second injection" in content


def test_agent_reads_injection_on_next_iter(tmp_path):
    """Agent prepends injected text to history when queue file is non-empty."""
    from orchid.agents.base import BaseAgent

    queue_path = tmp_path / "inject.queue"
    queue_path.write_text("use trafilatura not bs4\n", encoding="utf-8")

    agent = BaseAgent(injection_queue_path=queue_path)

    with patch("orchid.agents.base.call") as mock_call_fn:
        mock_call_fn.return_value = "Final Answer: done"
        agent.run("test task")

    # History should contain the injected message
    history_contents = [m.content for m in agent.history]
    injected = [c for c in history_contents if "Injected context from user" in c]
    assert len(injected) == 1
    assert "use trafilatura not bs4" in injected[0]


def test_injection_cleared_after_read(tmp_path):
    """After agent reads the queue, it should be empty."""
    from orchid.agents.base import BaseAgent

    queue_path = tmp_path / "inject.queue"
    queue_path.write_text("clear me after reading\n", encoding="utf-8")

    agent = BaseAgent(injection_queue_path=queue_path)

    with patch("orchid.agents.base.call") as mock_call_fn:
        mock_call_fn.return_value = "Final Answer: done"
        agent.run("test task")

    # Queue should be cleared
    assert queue_path.read_text(encoding="utf-8").strip() == ""


def test_no_injection_when_queue_empty(tmp_path):
    """Agent does not inject anything when queue is empty."""
    from orchid.agents.base import BaseAgent

    queue_path = tmp_path / "inject.queue"
    queue_path.write_text("", encoding="utf-8")

    agent = BaseAgent(injection_queue_path=queue_path)
    initial_history_len = 1  # just the task message

    with patch("orchid.agents.base.call") as mock_call_fn:
        mock_call_fn.return_value = "Final Answer: done"
        agent.run("test task")

    history_contents = [m.content for m in agent.history]
    injected = [c for c in history_contents if "Injected context from user" in c]
    assert len(injected) == 0


def test_no_injection_when_no_queue_path():
    """Agent with no injection_queue_path never tries to inject."""
    from orchid.agents.base import BaseAgent

    agent = BaseAgent(injection_queue_path=None)

    with patch("orchid.agents.base.call") as mock_call_fn:
        mock_call_fn.return_value = "Final Answer: done"
        agent.run("test task")

    history_contents = [m.content for m in agent.history]
    injected = [c for c in history_contents if "Injected context from user" in c]
    assert len(injected) == 0


def test_injection_injection_queue_missing_file_no_crash(tmp_path):
    """Agent handles missing queue file gracefully (no crash)."""
    from orchid.agents.base import BaseAgent

    queue_path = tmp_path / "nonexistent.queue"
    # File does not exist

    agent = BaseAgent(injection_queue_path=queue_path)

    with patch("orchid.agents.base.call") as mock_call_fn:
        mock_call_fn.return_value = "Final Answer: done"
        result = agent.run("test task")

    assert result == "done"
