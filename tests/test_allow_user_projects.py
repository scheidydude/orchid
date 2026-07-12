"""Tests for allow_user_projects enforcement.

Covers:
- POST /api/projects blocked for non-admin when flag disabled
- POST /api/projects allowed for admin when flag disabled
- POST /api/projects allowed for any user when flag enabled
- Telegram _cmd_new blocked when flag disabled
- Slack _handle_new blocked when flag disabled
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── API route — POST /api/projects ───────────────────────────────────────────

class TestCreateProjectFlagAPI:
    """POST /api/projects respects web.allow_user_projects."""

    def _make_app(self, tmp_path: Path, flag: bool, user_role: str = "user"):
        pytest = __import__("pytest")
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")

        from fastapi.testclient import TestClient

        import orchid.auth.middleware as mw
        from orchid.auth.types import User
        from orchid.interfaces.web_server import create_app

        user = User(
            user_id="alice",
            username="alice",
            role=user_role,
            is_active=True,
        )

        # Return user from get_optional_user
        async def _fake_optional():
            return user

        # Minimal create_app call with no project paths / bots
        with patch.object(mw, "get_optional_user", return_value=_fake_optional), \
             patch("orchid.config.get", side_effect=lambda k, d=None: flag if k == "web.allow_user_projects" else d):
            app = create_app(project_paths=[], enable_telegram=False, enable_slack=False)

        return TestClient(app, raise_server_exceptions=False)

    def test_blocked_for_non_admin_when_disabled(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient

        import orchid.auth.middleware as mw
        from orchid.auth.types import User

        user = User(user_id="bob", username="bob", role="user", is_active=True)

        async def _fake_optional_user():
            return user

        with patch.object(mw, "get_optional_user", _fake_optional_user), \
             patch("orchid.config.get", side_effect=lambda k, d=None: False if k == "web.allow_user_projects" else d), \
             patch("orchid.interfaces.web_server.get_optional_user", _fake_optional_user):
            from orchid.interfaces.web_server import create_app
            app = create_app(project_paths=[], enable_telegram=False, enable_slack=False)
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/api/projects", json={"name": "myapp", "description": "test"})

        assert r.status_code == 403
        assert "disabled" in r.json().get("detail", "").lower()

    def test_allowed_for_admin_when_disabled(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient

        import orchid.auth.middleware as mw
        from orchid.auth.types import User
        from orchid.project_creator import ProjectCreator

        admin = User(user_id="admin1", username="admin", role="admin", is_active=True)

        async def _fake_optional_admin():
            return admin

        fake_dir = tmp_path / "myapp"

        with patch.object(mw, "get_optional_user", _fake_optional_admin), \
             patch("orchid.interfaces.web_server.get_optional_user", _fake_optional_admin), \
             patch("orchid.config.get", side_effect=lambda k, d=None: False if k == "web.allow_user_projects" else d), \
             patch.object(ProjectCreator, "create", return_value=fake_dir), \
             patch.object(ProjectCreator, "confirm_path", return_value=fake_dir):
            fake_dir.mkdir(parents=True, exist_ok=True)
            from orchid.interfaces.web_server import create_app
            app = create_app(project_paths=[], enable_telegram=False, enable_slack=False)
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/api/projects", json={"name": "myapp", "description": "test"})

        # Admin bypasses flag — should NOT be 403
        assert r.status_code != 403

    def test_allowed_for_any_user_when_enabled(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient

        import orchid.auth.middleware as mw
        from orchid.auth.types import User
        from orchid.project_creator import ProjectCreator

        user = User(user_id="charlie", username="charlie", role="user", is_active=True)

        async def _fake_optional():
            return user

        fake_dir = tmp_path / "newproj"
        fake_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(mw, "get_optional_user", _fake_optional), \
             patch("orchid.interfaces.web_server.get_optional_user", _fake_optional), \
             patch("orchid.config.get", side_effect=lambda k, d=None: True if k == "web.allow_user_projects" else d), \
             patch.object(ProjectCreator, "create", return_value=fake_dir), \
             patch.object(ProjectCreator, "confirm_path", return_value=fake_dir):
            from orchid.interfaces.web_server import create_app
            app = create_app(project_paths=[], enable_telegram=False, enable_slack=False)
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/api/projects", json={"name": "newproj", "description": "test"})

        assert r.status_code != 403


# ── Telegram _cmd_new ─────────────────────────────────────────────────────────

class TestTelegramNewFlagCheck:
    """CentralTelegramBot._cmd_new checks web.allow_user_projects."""

    def _make_bot(self, tmp_path: Path):
        import threading

        from orchid.interfaces.telegram_central import CentralTelegramBot
        bot = object.__new__(CentralTelegramBot)
        bot._discovery = MagicMock()
        bot.token = "tok"
        bot.allowed_users = set()
        bot._state_file = tmp_path / "state.json"
        bot._user_state = {}
        bot._state_lock = threading.Lock()
        bot._runners = {}
        bot._runners_lock = threading.Lock()
        bot._subscribers = {}
        bot._sub_lock = threading.Lock()
        bot._app = None
        bot._loop = None
        bot._stop_event = None
        return bot

    def _make_update(self, text: str = "my project"):
        update = MagicMock()
        update.effective_user.id = 12345
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        return update

    def _make_ctx(self, args):
        ctx = MagicMock()
        ctx.args = args
        return ctx

    def _run(self, coro):
        """Run a coroutine without touching the main thread's event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_new_blocked_when_flag_disabled(self, tmp_path):
        bot = self._make_bot(tmp_path)
        update = self._make_update()
        ctx = self._make_ctx(["my", "project"])

        with patch("orchid.config.get", side_effect=lambda k, d=None: False if k == "web.allow_user_projects" else d):
            self._run(bot._cmd_new(update, ctx))

        # Should have replied with error — check reply_text was called with "disabled"
        call_args = update.message.reply_text.call_args
        assert call_args is not None
        replied_text = call_args[0][0] if call_args[0] else str(call_args)
        assert "disabled" in replied_text.lower()

    def test_new_proceeds_when_flag_enabled(self, tmp_path):
        bot = self._make_bot(tmp_path)
        update = self._make_update()
        ctx = self._make_ctx(["my", "project"])

        fake_path = tmp_path / "my-project"
        fake_path.mkdir()

        mock_reply = AsyncMock()
        with patch("orchid.config.get", side_effect=lambda k, d=None: True if k == "web.allow_user_projects" else d), \
             patch("orchid.project_creator.ProjectCreator.create", return_value=fake_path), \
             patch.object(bot, "_set_active_project"), \
             patch.object(bot, "_reply", mock_reply):
            self._run(bot._cmd_new(update, ctx))

        # Flag was True — no "disabled" error, _reply would have been called with success
        mock_reply.assert_called_once()
        text_sent = mock_reply.call_args[0][1]
        assert "disabled" not in text_sent.lower()


# ── Slack _handle_new ─────────────────────────────────────────────────────────

class TestSlackNewFlagCheck:
    """CentralSlackBot._handle_new checks web.allow_user_projects."""

    def _make_bot(self, tmp_path: Path):
        import asyncio
        import threading

        from orchid.interfaces.slack_central import CentralSlackBot

        bot = object.__new__(CentralSlackBot)
        bot._discovery = MagicMock()
        bot.bot_token = "xoxb"
        bot.app_token = "xapp"
        bot._channels_file = tmp_path / "channels.json"
        bot.auto_create_channels = False
        bot.default_channel = "#general"
        bot._channel_map = {}
        bot._map_lock = threading.Lock()
        bot._runners = {}
        bot._runners_lock = threading.Lock()
        bot._notify_channels = {}
        bot._notify_lock = threading.Lock()
        bot._loop = asyncio.new_event_loop()
        bot._loop_thread = threading.Thread(target=bot._loop.run_forever, daemon=True)
        bot._loop_thread.start()
        bot._client = None
        bot._handler = None
        return bot

    def test_handle_new_blocked_when_flag_disabled(self, tmp_path):
        bot = self._make_bot(tmp_path)
        ack = MagicMock()
        respond = MagicMock()
        say = MagicMock()
        command = {"text": "my new project", "channel_id": "C123"}

        with patch("orchid.config.get", side_effect=lambda k, d=None: False if k == "web.allow_user_projects" else d):
            bot._handle_new(ack, respond, say, command)

        ack.assert_called_once()
        respond.assert_called_once()
        assert "disabled" in respond.call_args[0][0].lower()
        say.assert_not_called()

    def test_handle_new_proceeds_when_flag_enabled(self, tmp_path):
        bot = self._make_bot(tmp_path)
        ack = MagicMock()
        respond = MagicMock()
        say = MagicMock()
        command = {"text": "my new project", "channel_id": "C123"}

        fake_path = tmp_path / "my-new-project"
        fake_path.mkdir()

        with patch("orchid.config.get", side_effect=lambda k, d=None: True if k == "web.allow_user_projects" else d), \
             patch("orchid.project_creator.ProjectCreator.create", return_value=fake_path):
            bot._handle_new(ack, respond, say, command)

        # Flag enabled — should NOT respond with disabled error
        if respond.called:
            assert "disabled" not in respond.call_args[0][0].lower()
        say.assert_called_once()
        assert "disabled" not in say.call_args.kwargs.get("text", "").lower()
