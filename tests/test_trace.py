"""Tests for --trace flag / TraceWriter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.orchestrator import TraceWriter


# ── TraceWriter unit tests ────────────────────────────────────────────────────


def test_trace_log_created(tmp_path):
    tw = TraceWriter(tmp_path)
    tw.task_start("T001", "Build something")
    assert tw._path.exists()
    content = tw._path.read_text(encoding="utf-8")
    assert "Starting T001" in content
    assert "Build something" in content


def test_trace_log_appends(tmp_path):
    tw = TraceWriter(tmp_path)
    tw.task_start("T001", "First task")
    tw.task_start("T002", "Second task")
    content = tw._path.read_text(encoding="utf-8")
    assert "T001" in content
    assert "T002" in content


def test_trace_iteration_format(tmp_path):
    tw = TraceWriter(tmp_path)
    tw.iteration(
        task_id="T001",
        iter_num=2,
        max_iter=10,
        elapsed=1.23,
        thought="I need to read the file",
        action="read_file",
        action_input='{"path": "src/main.py"}',
        observation="File contents here",
    )
    content = tw._path.read_text(encoding="utf-8")
    assert "--- T001 iter 2/10 (1.2s) ---" in content
    assert "THOUGHT: I need to read the file" in content
    assert "ACTION:  read_file" in content
    assert 'INPUT:   {"path": "src/main.py"}' in content
    assert "OBS:     File contents here" in content


def test_trace_truncation(tmp_path):
    tw = TraceWriter(tmp_path)
    long_thought = "x" * 200
    tw.iteration(
        task_id="T001",
        iter_num=1,
        max_iter=10,
        elapsed=0.5,
        thought=long_thought,
        action="bash",
        action_input='{"command": "ls"}',
        observation="file.py",
    )
    content = tw._path.read_text(encoding="utf-8")
    assert "(truncated)" in content


def test_trace_summary_done(tmp_path):
    tw = TraceWriter(tmp_path)
    tw.task_summary(
        task_id="T001",
        status="done",
        completed_iters=3,
        max_iter=10,
        action_counts={"write_file": 2, "read_file": 1},
        elapsed=8.5,
    )
    content = tw._path.read_text(encoding="utf-8")
    assert "=== T001 DONE in 3/10 iters" in content
    assert "write_file×2" in content
    assert "read_file×1" in content
    assert "8.5s ===" in content


def test_trace_summary_blocked(tmp_path):
    tw = TraceWriter(tmp_path)
    tw.task_summary(
        task_id="T002",
        status="blocked",
        completed_iters=10,
        max_iter=10,
        action_counts={"bash": 3},
        elapsed=45.2,
    )
    content = tw._path.read_text(encoding="utf-8")
    assert "=== T002 BLOCKED at 10/10 iters" in content
    assert "bash×3" in content


def test_trace_dir_created_automatically(tmp_path):
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    # .orchid does NOT exist yet
    tw = TraceWriter(project_dir)
    tw.task_start("T001", "Test")
    assert (project_dir / ".orchid" / "trace.log").exists()


# ── Integration: orchestrator wires trace ────────────────────────────────────


def _make_session(tmp_path: Path):
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n- [ ] **T001** Trace test `type:draft` `p1`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Test project", encoding="utf-8")
    with patch("orchid.memory.vector.VectorMemory.__init__", return_value=None), \
         patch("orchid.memory.vector.VectorMemory.available",
               new_callable=lambda: property(lambda self: False)):
        from orchid.session import Session
        session = Session(project_dir=tmp_path)
        session._vector = None
        session.load()
    return session


def test_orchestrator_trace_enabled_creates_writer(tmp_path):
    session = _make_session(tmp_path)
    from orchid.orchestrator import Orchestrator
    orch = Orchestrator(session, trace_enabled=True)
    assert orch._trace_writer is not None
    assert isinstance(orch._trace_writer, TraceWriter)


def test_orchestrator_trace_disabled_no_writer(tmp_path):
    session = _make_session(tmp_path)
    from orchid.orchestrator import Orchestrator
    orch = Orchestrator(session, trace_enabled=False)
    assert orch._trace_writer is None


def test_orchestrator_trace_log_written_on_task(tmp_path):
    """Full trace log is written when a task runs with trace_enabled=True."""
    session = _make_session(tmp_path)
    from orchid.orchestrator import Orchestrator
    orch = Orchestrator(session, trace_enabled=True)

    with patch("orchid.agents.base.call") as mock_call, \
         patch.object(session, "stream_react"), \
         patch.object(session, "save"), \
         patch.object(session, "update_task_status"), \
         patch.object(session, "log_event"), \
         patch.object(session, "context_block", return_value="## Context\nTest project"), \
         patch("orchid.memory.state.TaskResultStore.append"), \
         patch("orchid.memory.state.save_tasks"), \
         patch.object(orch, "_update_hot_memory"):
        mock_call.return_value = "Final Answer: done"
        task = session.tasks[0]
        orch._execute_task(task)

    trace_log = tmp_path / ".orchid" / "trace.log"
    assert trace_log.exists()
    content = trace_log.read_text(encoding="utf-8")
    assert "T001" in content
    assert "DONE" in content or "BLOCKED" in content


def test_stream_callback_has_trace_state(tmp_path):
    """_make_stream_callback attaches _trace_state to the returned callback."""
    session = _make_session(tmp_path)
    from orchid.orchestrator import Orchestrator
    orch = Orchestrator(session, trace_enabled=True)
    cb = orch._make_stream_callback("T001", "Test task")
    assert hasattr(cb, "_trace_state")
    assert "completed_iters" in cb._trace_state
    assert "action_counts" in cb._trace_state


def test_action_input_in_stream_callback():
    """BaseAgent includes action_input in stream callback data."""
    from orchid.agents.base import BaseAgent

    calls: list[dict] = []

    agent = BaseAgent(stream_callback=calls.append)

    with patch("orchid.agents.base.call") as mock_call:
        mock_call.side_effect = [
            "Thought: check dir\nAction: list_dir\nAction Input: {\"path\": \"src\"}",
            "Final Answer: done",
        ]
        agent.run("test task")

    tool_call = next((c for c in calls if c["action"] == "list_dir"), None)
    assert tool_call is not None
    assert "action_input" in tool_call
    assert "src" in tool_call["action_input"]
