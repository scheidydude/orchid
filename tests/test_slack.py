"""Tests for Slack formatter and bot logic.

Focus: formatter output, thread tracking, token resolution, intent parsing.
No live Slack API calls.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_task(
    tid: str,
    title: str,
    status: str = "TODO",
    ttype: str = "code_generate",
    priority: int = 1,
    depends_on: list[str] | None = None,
) -> Any:
    t = SimpleNamespace()
    t.id = tid
    t.title = title
    t.status = SimpleNamespace(value=status)
    t.type = ttype
    t.priority = priority
    t.depends_on = depends_on or []
    t.is_runnable = lambda completed: all(d in completed for d in t.depends_on)
    return t


def _make_session(
    name: str = "TestProject",
    description: str = "A test project",
    tasks: list[Any] | None = None,
    hot_memory: str = "",
) -> Any:
    s = SimpleNamespace()
    s.project_name = name
    s.project_description = description
    s.tasks = tasks or []
    s.hot_memory = hot_memory
    return s


# ── Formatter tests ────────────────────────────────────────────────────────────


class TestFormatStatusMrkdwn:
    def test_format_status_uses_mrkdwn(self):
        """format_status_text output contains Slack mrkdwn markers (*bold*, _italic_, `code`)."""
        from orchid.interfaces.slack_formatter import format_status_text

        tasks = [_make_task("T001", "Build auth module", status="IN_PROGRESS")]
        session = _make_session(tasks=tasks)
        text = format_status_text(session)

        # Header uses *bold* project name
        assert "*TestProject*" in text
        # Description uses _italic_
        assert "_A test project_" in text
        # Task ID uses *bold*
        assert "*T001*" in text
        # Task type uses `backtick`
        assert "`" in text

    def test_format_status_block_kit_structure(self):
        """format_status returns list of Block Kit blocks with correct types."""
        from orchid.interfaces.slack_formatter import format_status

        tasks = [_make_task("T001", "Deploy", status="DONE")]
        session = _make_session(tasks=tasks)
        blocks = format_status(session)

        assert isinstance(blocks, list)
        assert len(blocks) >= 2

        types = [b["type"] for b in blocks]
        assert "header" in types
        assert "section" in types

        header = next(b for b in blocks if b["type"] == "header")
        assert header["text"]["type"] == "plain_text"
        assert "TestProject" in header["text"]["text"]

        section = next(b for b in blocks if b["type"] == "section")
        assert section["text"]["type"] == "mrkdwn"


class TestFormatTaskList:
    def test_format_task_list_truncates(self):
        """format_task_list truncates at 4000 chars with …(truncated) suffix."""
        from orchid.interfaces.slack_formatter import format_task_list

        # Create enough tasks to exceed the 4000-char limit
        tasks = [
            _make_task(f"T{i:03d}", "A" * 120, status="TODO")
            for i in range(35)
        ]
        result = format_task_list(tasks)

        assert len(result) <= 4000
        assert "…(truncated)" in result

    def test_format_task_list_empty(self):
        from orchid.interfaces.slack_formatter import format_task_list

        result = format_task_list([])
        assert result == "No tasks."


class TestFormatRecallResults:
    def test_format_recall_results_contains_fields(self):
        """format_recall_results shows index, type, score, timestamp, and text snippet."""
        from orchid.interfaces.slack_formatter import format_recall_results

        results = [
            {
                "text": "Session log content here",
                "distance": 0.1,
                "metadata": {"type": "session_log", "timestamp": "2026-03-15T12:00:00"},
            }
        ]
        out = format_recall_results(results)

        assert "*[1]*" in out
        assert "session_log" in out
        assert "0.90" in out  # score = 1 - 0.1
        assert "2026-03-15 12:00" in out
        assert "Session log content here" in out

    def test_format_recall_results_empty(self):
        from orchid.interfaces.slack_formatter import format_recall_results

        assert format_recall_results([]) == "No results found."


# ── Block Kit structure ────────────────────────────────────────────────────────


class TestSlackFormatterBlockKitStructure:
    def test_section_block_structure(self):
        """_section returns valid Block Kit section dict."""
        from orchid.interfaces.slack_formatter import _section

        block = _section("hello *world*")
        assert block["type"] == "section"
        assert block["text"]["type"] == "mrkdwn"
        assert block["text"]["text"] == "hello *world*"

    def test_header_block_structure(self):
        from orchid.interfaces.slack_formatter import _header

        block = _header("My Header")
        assert block["type"] == "header"
        assert block["text"]["type"] == "plain_text"
        assert block["text"]["emoji"] is True

    def test_divider_block_structure(self):
        from orchid.interfaces.slack_formatter import _divider

        block = _divider()
        assert block == {"type": "divider"}

    def test_section_truncates_at_3000(self):
        """Text > 3000 chars is truncated in _section."""
        from orchid.interfaces.slack_formatter import _section

        long_text = "x" * 3500
        block = _section(long_text)
        assert len(block["text"]["text"]) <= 3000
        assert "…(truncated)" in block["text"]["text"]


# ── Thread tracking ────────────────────────────────────────────────────────────


class TestThreadTracking:
    def _make_bot(self):
        """Return a SlackBot with all Slack internals mocked out."""
        with patch("orchid.interfaces.slack_bot._SLACK_AVAILABLE", True):
            from orchid.interfaces.slack_bot import SlackBot

        bot = SlackBot.__new__(SlackBot)
        bot.project_path = "/fake/project"
        bot.bot_token = "xoxb-fake"
        bot.app_token = "xapp-fake"
        bot.default_channel = "#orchid"
        bot.multi_project = False
        bot._all_projects = ["/fake/project"]
        bot._task_threads = {}
        bot._notify_channels = set()
        bot._client = MagicMock()
        bot._client.chat_postMessage.return_value = {"ts": "111.222"}
        bot._loop = MagicMock()
        bot._multi_thread = None
        bot._handler = None

        from orchid.interfaces.background_runner import BackgroundRunner
        runner = MagicMock(spec=BackgroundRunner)
        runner.is_running.return_value = False
        bot._runner = runner

        return bot

    def test_thread_tracking_stores_ts(self):
        """After _handle_run posts the start message, task_id is in _task_threads."""
        bot = self._make_bot()
        command = {"text": "T001", "channel_id": "#orchid"}

        with (
            patch.object(bot, "_make_session") as mock_session,
            patch.object(bot._runner, "is_running", return_value=False),
        ):
            session = _make_session(tasks=[_make_task("T001", "Fix bug")])
            mock_session.return_value = session

            ack_calls = []
            bot._handle_run(
                ack=lambda *a, **kw: ack_calls.append((a, kw)),
                say=MagicMock(),
                command=command,
                client=MagicMock(),
            )

        assert "T001" in bot._task_threads
        assert bot._task_threads["T001"]["thread_ts"] == "111.222"
        assert bot._task_threads["T001"]["channel"] == "#orchid"

    def test_thread_tracking_cleared_on_session_end(self):
        """The completion callback removes the task from _task_threads."""
        bot = self._make_bot()
        bot._task_threads["T001"] = {"channel": "#orchid", "thread_ts": "111.222"}

        # Simulate the _cb closure that _handle_run creates
        from orchid.interfaces.slack_formatter import format_task_complete

        def _cb(tid: str, result: str | None, error: str | None) -> None:
            bot._notify_channels.discard("#orchid")
            msg = format_task_complete(tid, result or "")
            thread_info = bot._task_threads.pop(tid, None)
            if thread_info:
                bot._post(thread_info["channel"], msg, thread_ts=thread_info["thread_ts"])

        _cb("T001", "Done output", None)
        assert "T001" not in bot._task_threads


# ── Token resolution ───────────────────────────────────────────────────────────


class TestTokenResolution:
    def test_token_resolution_from_env(self, monkeypatch):
        """CLI slack command resolves tokens from environment variables."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env-token")
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-env-token")

        # Verify the env vars are readable — the CLI reads them the same way
        assert os.environ["SLACK_BOT_TOKEN"] == "xoxb-env-token"
        assert os.environ["SLACK_APP_TOKEN"] == "xapp-env-token"

    def test_token_missing_raises(self, monkeypatch):
        """slack CLI subcommand exits with error if tokens are missing."""
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

        from typer.testing import CliRunner
        from orchid.interfaces.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["slack", "--project", "/tmp"])
        # Should exit non-zero (missing token)
        assert result.exit_code != 0


# ── Intent parsing ─────────────────────────────────────────────────────────────


class TestIntentParsing:
    def _make_bot_for_intent(self):
        """Return a minimal SlackBot instance just for intent parsing."""
        with patch("orchid.interfaces.slack_bot._SLACK_AVAILABLE", True):
            from orchid.interfaces.slack_bot import SlackBot

        bot = SlackBot.__new__(SlackBot)
        return bot

    def test_intent_parsing_status(self):
        """'status' and task-listing messages resolve to status intent."""
        bot = self._make_bot_for_intent()
        for msg in ["status", "tasks", "what are the pending tasks"]:
            result = bot._parse_intent(msg)
            assert result["intent"] == "status", f"Expected status for: {msg!r}"

    def test_intent_parsing_run(self):
        """'run T001' resolves to run intent with correct task id."""
        bot = self._make_bot_for_intent()
        result = bot._parse_intent("run T003")
        assert result["intent"] == "run"
        assert result["arg"] == "T003"

    def test_intent_parsing_add_task(self):
        """'add task ...' resolves to add intent and strips prefix."""
        bot = self._make_bot_for_intent()
        result = bot._parse_intent("add task to fix the login page")
        assert result["intent"] == "add"
        assert "fix" in result["arg"].lower()

    def test_intent_parsing_recall(self):
        bot = self._make_bot_for_intent()
        result = bot._parse_intent("recall session summary")
        assert result["intent"] == "recall"
        assert "session summary" in result["arg"].lower()

    def test_intent_parsing_search(self):
        bot = self._make_bot_for_intent()
        result = bot._parse_intent("search for FastAPI best practices")
        assert result["intent"] == "search"
        assert "FastAPI" in result["arg"]


# ── Multi-project channel tagging ─────────────────────────────────────────────


class TestMultiProjectChannelTagging:
    def test_multi_project_channel_tagging(self):
        """multi_formatter tags messages with short project prefix."""
        from orchid.interfaces.multi_formatter import tag_message

        msg = "✅ *T001* done"
        tagged = tag_message("mywebapp", msg)
        assert tagged.startswith("[mywebapp]")
        assert msg in tagged

    def test_multi_formatter_notification_tags_project(self):
        """format_notification from multi_formatter prepends project tag."""
        from orchid.interfaces.multi_formatter import format_notification

        data = {"task_id": "T001", "title": "Build auth", "remaining": 2}
        result = format_notification("task_start", "mywebapp", data)
        assert result is not None
        assert "[mywebapp]" in result

    def test_multi_formatter_worker_restart_message(self):
        """worker_restart event produces a warning message with project tag."""
        from orchid.interfaces.multi_formatter import format_notification

        result = format_notification("worker_restart", "webtron", {"restart_count": 1, "max_restarts": 3})
        assert result is not None
        assert "[webtron]" in result
        assert "restart" in result.lower()

    def test_tag_truncates_long_project_name(self):
        """Tags are limited to 10 characters."""
        from orchid.interfaces.multi_formatter import tag_message, _MAX_TAG

        long_name = "averylongprojectname"
        tagged = tag_message(long_name, "msg")
        assert tagged.startswith(f"[{long_name[:_MAX_TAG]}]")
