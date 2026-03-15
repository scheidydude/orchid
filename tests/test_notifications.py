"""Tests for proactive Telegram notifications."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call as mock_call

import pytest

from orchid.interfaces.telegram_formatter import format_notification


# ── format_notification tests ─────────────────────────────────────────────────

def test_session_start_notification():
    msg = format_notification("session_start", {"project": "myapp", "pending": 5})
    assert msg is not None
    assert "5" in msg
    assert "myapp" in msg
    assert "🌸" in msg


def test_task_start_notification():
    msg = format_notification("task_start", {"task_id": "T003", "title": "Build CSS", "remaining": 4})
    assert msg is not None
    assert "T003" in msg
    assert "🤖" in msg


def test_task_complete_notification():
    msg = format_notification("task_complete", {"task_id": "T003", "result_snippet": "Done!", "done_so_far": 2})
    assert msg is not None
    assert "T003" in msg
    assert "✅" in msg


def test_task_failed_notification():
    msg = format_notification("task_failed", {"task_id": "T003", "error": "timeout"})
    assert msg is not None
    assert "T003" in msg
    assert "❌" in msg
    assert "timeout" in msg


def test_task_blocked_notification():
    msg = format_notification("task_blocked", {"task_id": "T003", "waiting_on": ["T001", "T002"]})
    assert msg is not None
    assert "T003" in msg
    assert "⚠️" in msg
    assert "T001" in msg


def test_session_complete_notification():
    msg = format_notification("session_complete", {"done": ["T001", "T002"], "failed": []})
    assert msg is not None
    assert "🎉" in msg
    assert "2/2" in msg


def test_session_complete_with_failures():
    msg = format_notification("session_complete", {"done": ["T001"], "failed": ["T002"]})
    assert msg is not None
    assert "1/2" in msg
    assert "T002" in msg


def test_unknown_event_returns_none():
    msg = format_notification("unknown_event", {})
    assert msg is None


def test_notification_config_respected(tmp_path):
    """BackgroundRunner respects telegram.notify_on config."""
    from orchid import config as cfg

    notifications: list[tuple] = []

    async def capture(event, data):
        notifications.append((event, data))

    # Patch config to only allow session_complete
    original_get = cfg.get

    def patched_get(key, default=None):
        if key == "telegram.notify_on":
            return ["session_complete"]
        return original_get(key, default)

    from orchid.interfaces.background_runner import BackgroundRunner

    runner = BackgroundRunner(str(tmp_path), notification_callback=capture)

    loop = asyncio.new_event_loop()

    with patch.object(cfg, "get", side_effect=patched_get):
        # task_start is not in the allow list — should be filtered
        runner._notify(loop, "task_start", {"task_id": "T001"})

    # Run the loop briefly to process any scheduled coroutines
    loop.run_until_complete(asyncio.sleep(0.01))
    loop.close()

    # No notification should have been sent for task_start
    assert len(notifications) == 0


def test_session_start_notification_sent(tmp_path):
    """session_start event fires notification when in notify_on list."""
    from orchid.interfaces.background_runner import BackgroundRunner

    received: list[tuple] = []

    async def capture(event, data):
        received.append((event, data))

    runner = BackgroundRunner(str(tmp_path), notification_callback=capture)
    loop = asyncio.new_event_loop()

    runner._notify(loop, "session_start", {"project": "test", "pending": 3})

    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()

    assert any(e == "session_start" for e, _ in received)
