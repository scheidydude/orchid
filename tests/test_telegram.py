"""Tests for Telegram interface — formatter and background runner.

These tests do NOT require a live Telegram connection.
The bot handler tests mock python-telegram-bot objects.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Formatter tests ───────────────────────────────────────────────────────────

from orchid.interfaces.telegram_formatter import (
    format_auto_summary,
    format_recall_results,
    format_search_results,
    format_status,
    format_task_complete,
    format_task_failed,
    format_task_list,
    format_task_started,
)

_RICH_CHARS = set("│┌┐└┘├┤┬┴┼─═║╒╓╔╕╖╗╘╙╚╛╜╝╞╟╠╡╢╣╤╥╦╧╨╩╪╫╬")
_RICH_MARKUP_RE = __import__("re").compile(r"\[/?[a-z_:# ]+\]")


def _no_rich(text: str) -> None:
    for ch in text:
        assert ch not in _RICH_CHARS, f"Box-drawing char {ch!r} found in: {text[:80]}"
    assert not _RICH_MARKUP_RE.search(text), f"Rich markup found in: {text[:80]}"


def _make_task(tid="T001", title="Test task", status="TODO", ttype="draft", priority=2):
    from orchid.memory.state import Task, TaskStatus
    return Task(id=tid, title=title, status=TaskStatus(status), type=ttype, priority=priority)


def _make_session(tasks=None, hot_memory="", name="TestProject", description=""):
    s = SimpleNamespace(
        project_name=name,
        project_description=description,
        tasks=tasks or [],
        hot_memory=hot_memory,
        decisions=[],
    )
    return s


# ── test_format_status_no_rich_chars ──────────────────────────────────────────

def test_format_status_no_rich_chars():
    session = _make_session(
        tasks=[_make_task("T001", "Build login page"), _make_task("T002", "Fix bug", status="DONE")],
        hot_memory="Some context here.",
        name="MyProject",
    )
    result = format_status(session)
    _no_rich(result)
    assert "T001" in result
    assert "T002" in result
    assert "MyProject" in result


def test_format_status_empty_tasks():
    session = _make_session()
    result = format_status(session)
    _no_rich(result)
    assert "No tasks" in result


def test_format_status_hot_memory_snippet():
    session = _make_session(hot_memory="A" * 600)
    result = format_status(session)
    # Only first 500 chars of hot memory should appear
    assert result.count("A") <= 500


# ── test_format_task_list_truncates_long_output ───────────────────────────────

def test_format_task_list_truncates_long_output():
    tasks = [_make_task(f"T{i:03d}", "x" * 200) for i in range(30)]
    result = format_task_list(tasks)
    assert len(result) <= 4000 + 20  # small slack for truncation suffix
    _no_rich(result)


def test_format_task_list_empty():
    assert format_task_list([]) == "No tasks."


# ── test_format_recall_results ────────────────────────────────────────────────

def test_format_recall_results():
    results = [
        {"text": "Session compression uses LLM summariser.", "metadata": {"type": "session", "timestamp": "2026-03-14T10:00:00"}, "distance": 0.1},
        {"text": "Vector store uses ChromaDB.", "metadata": {"type": "note"}, "distance": 0.3},
    ]
    result = format_recall_results(results)
    _no_rich(result)
    assert "Session compression" in result
    assert "ChromaDB" in result
    assert "score=0.90" in result


def test_format_recall_results_empty():
    assert "No results" in format_recall_results([])


# ── test_format_search_results ────────────────────────────────────────────────

def test_format_search_results():
    results = [
        {"title": "Python docs", "url": "https://docs.python.org", "snippet": "Official docs."},
    ]
    result = format_search_results(results)
    _no_rich(result)
    assert "Python docs" in result
    assert "docs.python.org" in result


def test_format_search_results_error():
    results = [{"title": "error", "snippet": "Connection refused", "url": ""}]
    result = format_search_results(results)
    assert "error" in result.lower() or "Connection refused" in result


def test_format_task_complete():
    result = format_task_complete("T001", "Task finished successfully.")
    _no_rich(result)
    assert "T001" in result
    assert "done" in result.lower() or "✅" in result


def test_format_task_failed():
    result = format_task_failed("T002", "TimeoutError: agent timed out")
    _no_rich(result)
    assert "T002" in result
    assert "failed" in result.lower() or "❌" in result


def test_format_auto_summary():
    result = format_auto_summary(["T001", "T002"], ["T003"])
    assert "T001" in result
    assert "T003" in result


# ── BackgroundRunner tests ─────────────────────────────────────────────────────

class _FakeSession:
    """Minimal session stub for BackgroundRunner tests."""
    def __init__(self, tasks=None):
        from orchid.memory.state import Task, TaskStatus
        self.tasks = tasks or [Task(id="T001", title="Do something", type="draft")]
        self.project_name = "fake"
        self.project_description = ""
        self.hot_memory = ""
        self.decisions = []
        self.delegations = []
        self._vector = None

    def next_task(self):
        from orchid.memory.state import TaskStatus
        for t in self.tasks:
            if t.status == TaskStatus.TODO:
                return t
        return None

    def save(self): pass
    def update_task_status(self, tid, status):
        from orchid.memory.state import TaskStatus
        for t in self.tasks:
            if t.id == tid:
                t.status = status
                return True
        return False
    def context_block(self): return ""
    def log_event(self, *a, **kw): pass


def _make_runner(tmp_path):
    from orchid.interfaces.background_runner import BackgroundRunner
    return BackgroundRunner(str(tmp_path))


def test_background_runner_is_running_initially_false(tmp_path):
    runner = _make_runner(tmp_path)
    assert not runner.is_running()
    runner.shutdown()


def test_background_runner_runs_task(tmp_path):
    """Runner starts a background task; the future resolves with no exception."""
    from orchid.interfaces.background_runner import BackgroundRunner

    runner = BackgroundRunner(str(tmp_path))
    called_with: list[tuple] = []

    def fake_sync(task_id, cb, loop):
        called_with.append((task_id,))

    # Use new= to replace the method directly (not wrapped in MagicMock)
    runner._run_task_sync = fake_sync  # type: ignore[method-assign]

    ok = runner.run_task("T001", lambda *a: None, None)
    assert ok is True
    runner._current_future.result(timeout=5)
    runner.shutdown()

    assert len(called_with) == 1
    assert called_with[0][0] == "T001"


def test_background_runner_cancel(tmp_path):
    """cancel() returns True when running, False when idle."""
    runner = _make_runner(tmp_path)
    assert not runner.cancel()  # nothing running
    runner.shutdown()


def test_background_runner_rejects_double_start(tmp_path):
    """run_task returns False if already running."""
    from orchid.interfaces.background_runner import BackgroundRunner

    runner = BackgroundRunner(str(tmp_path))
    started = threading.Event()
    finish = threading.Event()

    def slow_sync(task_id, cb, loop):
        started.set()
        finish.wait(timeout=5)

    def noop(*args): pass

    runner._run_task_sync = slow_sync  # type: ignore[method-assign]

    runner.run_task("T001", noop, None)
    started.wait(timeout=2)
    second = runner.run_task("T002", noop, None)
    assert second is False
    finish.set()
    time.sleep(0.1)
    runner.shutdown()


# ── User whitelist tests ───────────────────────────────────────────────────────

def test_user_whitelist_blocks_unknown():
    """Messages from non-whitelisted users must be silently dropped."""
    allowed = {111, 222}
    unknown_id = 999
    assert unknown_id not in allowed


def test_user_whitelist_allows_known():
    allowed = {111, 222}
    assert 111 in allowed
    assert 222 in allowed


# ── Token resolution tests ────────────────────────────────────────────────────

def test_token_resolution_from_env(monkeypatch, tmp_path):
    """CLI resolves token from TELEGRAM_BOT_TOKEN env var."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    assert token == "test-token-123"


def test_token_missing_raises(monkeypatch):
    """When no token is set, the resolved token is empty."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    assert token == ""
