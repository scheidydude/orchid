"""Tests for real-time streaming logs."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.session import Session


def _make_session(tmp_path: Path) -> Session:
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n- [ ] **T001** Test task `type:draft` `p1`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Test project", encoding="utf-8")
    with patch("orchid.memory.vector.VectorMemory.__init__", return_value=None), \
         patch("orchid.memory.vector.VectorMemory.available", new_callable=lambda: property(lambda self: False)):
        session = Session(project_dir=tmp_path)
        session._vector = None
        session.load()
    return session


def test_live_log_created_on_session_start(tmp_path):
    session = _make_session(tmp_path)
    assert session._live_log_path is not None
    # File path should end with .live.log
    assert str(session._live_log_path).endswith(".live.log")


def test_live_log_appended_each_iteration(tmp_path):
    session = _make_session(tmp_path)

    # Write two iterations
    session.stream_react({
        "iter": 0,
        "thought": "Let me think about this",
        "action": "read_file",
        "observation": "file contents here",
        "timestamp": "2026-03-15T12:00:00+00:00",
    })
    session.stream_react({
        "iter": 1,
        "thought": "Now I will write",
        "action": "write_file",
        "observation": "written successfully",
        "timestamp": "2026-03-15T12:00:01+00:00",
    })

    assert session._live_log_path is not None
    assert session._live_log_path.exists()
    content = session._live_log_path.read_text(encoding="utf-8")

    assert "iter=0" in content
    assert "Let me think about this" in content
    assert "iter=1" in content
    assert "Now I will write" in content


def test_live_log_renamed_on_session_end(tmp_path):
    session = _make_session(tmp_path)

    session.stream_react({
        "iter": 0, "thought": "test", "action": "bash", "observation": "ok",
        "timestamp": "2026-03-15T12:00:00+00:00",
    })

    live_path = session._live_log_path
    assert live_path is not None
    assert live_path.exists()

    # Close renames .live.log → .log
    with patch.object(session, "_maybe_compress_hot_memory"), \
         patch.object(session, "_auto_embed_session"):
        session.close(summary="test")

    assert not live_path.exists()
    # session_X.live.log → session_X.log
    name = live_path.name
    final_path = live_path.parent / (name[: -len(".live.log")] + ".log")
    assert final_path.exists()


def test_live_log_no_crash_when_disabled(tmp_path):
    """stream_react() should silently no-op when live log is disabled."""
    from orchid import config as cfg
    with patch.object(cfg, "get", side_effect=lambda k, d=None: False if k == "streaming.enabled" else cfg.get.__wrapped__(k, d) if hasattr(cfg.get, "__wrapped__") else d):
        session = Session(project_dir=tmp_path)
        session._live_log_path = None  # disabled

    # Should not raise
    session.stream_react({"iter": 0, "thought": "x", "action": "y", "observation": "z"})


def test_tail_reads_live_log(tmp_path):
    """The live log file is plain text and can be read line by line (simulates tail)."""
    session = _make_session(tmp_path)

    for i in range(5):
        session.stream_react({
            "iter": i,
            "thought": f"Thinking step {i}",
            "action": "read_file",
            "observation": f"Result {i}",
            "timestamp": f"2026-03-15T12:00:0{i}+00:00",
        })

    assert session._live_log_path is not None
    content = session._live_log_path.read_text(encoding="utf-8")
    for i in range(5):
        assert f"iter={i}" in content
        assert f"Thinking step {i}" in content


def test_maybe_compress_hot_memory_above_threshold(tmp_path):
    """_maybe_compress_hot_memory() should call the LLM and replace hot_memory when over threshold."""
    session = _make_session(tmp_path)
    session.hot_memory = "word " * 1500  # ~7500 chars, exceeds 6000-char threshold

    compressed = "# Compressed CLAUDE.md\n\nEssential facts only."
    with patch("orchid.tools.models.call", return_value=compressed):
        session._maybe_compress_hot_memory()

    assert "<!-- compressed" in session.hot_memory
    assert compressed in session.hot_memory


def test_maybe_compress_hot_memory_below_threshold(tmp_path):
    """_maybe_compress_hot_memory() should be a no-op when under the threshold."""
    session = _make_session(tmp_path)
    session.hot_memory = "short content"

    with patch("orchid.tools.models.call") as mock_call:
        session._maybe_compress_hot_memory()

    mock_call.assert_not_called()
    assert session.hot_memory == "short content"


def test_maybe_compress_hot_memory_survives_llm_failure(tmp_path):
    """_maybe_compress_hot_memory() should not raise if the LLM call fails."""
    session = _make_session(tmp_path)
    original = "word " * 1500
    session.hot_memory = original

    with patch("orchid.tools.models.call", side_effect=RuntimeError("API down")):
        session._maybe_compress_hot_memory()  # must not raise

    # Hot memory should be unchanged on failure
    assert session.hot_memory == original


def test_stream_callback_called_in_agent(tmp_path):
    """BaseAgent calls stream_callback after each ReAct iteration."""
    from orchid.agents.base import BaseAgent

    calls: list[dict] = []

    def capture(data):
        calls.append(data)

    agent = BaseAgent(stream_callback=capture)

    # Simulate one iteration by patching call()
    with patch("orchid.agents.base.call") as mock_call:
        mock_call.return_value = "Final Answer: done"
        agent.run("test task")

    assert len(calls) == 1
    assert calls[0]["action"] == "final_answer"
    assert "iter" in calls[0]
    assert "timestamp" in calls[0]
