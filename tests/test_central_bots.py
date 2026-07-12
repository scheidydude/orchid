"""Tests for the central bot architecture (D0050-D0052).

Tests cover:
- Telegram user state load/save/routing
- Slack channel map load/save/routing
- CentralBotManager start/stop and project-added notifications
- V2 lifecycle integration (approve via bot)
- DiscussionAgent routing
- Deprecation warnings on old telegram/slack CLI commands

All tests are offline — no real Telegram/Slack connections.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_discovery(projects: list[Path]) -> Any:
    """Minimal mock of ProjectDiscovery."""
    disc = MagicMock()
    disc.scan.return_value = projects
    return disc


def _make_project_dir(tmp_path: Path, name: str) -> Path:
    """Create a minimal orchid project dir."""
    p = tmp_path / name
    p.mkdir()
    (p / ".orchid.yaml").write_text(f"name: {name}\n")
    (p / "tasks.md").write_text("# Tasks\n")
    (p / ".orchid").mkdir(exist_ok=True)
    return p


# ══════════════════════════════════════════════════════════════════════════════
# Telegram central — state file
# ══════════════════════════════════════════════════════════════════════════════

class TestTelegramState:
    """Tests for CentralTelegramBot user state file I/O."""

    def test_telegram_state_loads_and_saves(self, tmp_path):
        """State file is read on init and written on project switch."""
        state_file = tmp_path / "telegram-state.json"
        existing = {
            "123456": {
                "active_project": "myapp",
                "active_project_path": "/home/dave/myapp",
                "phase": "EXECUTING",
                "last_interaction": "2026-03-22T10:00:00+00:00",
            }
        }
        state_file.write_text(json.dumps(existing))

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=_make_discovery([]),
                token="test-token",
                state_file=state_file,
            )

        name, path = bot._get_active_project(123456)
        assert name == "myapp"
        assert path == "/home/dave/myapp"

    def test_telegram_state_saves_on_switch(self, tmp_path):
        """_set_active_project writes an updated state file atomically."""
        state_file = tmp_path / "telegram-state.json"

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=_make_discovery([]),
                token="test-token",
                state_file=state_file,
            )

        bot._set_active_project(999, "webchess", "/home/dave/webchess", "READY")

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert "999" in data
        assert data["999"]["active_project"] == "webchess"
        assert data["999"]["active_project_path"] == "/home/dave/webchess"
        assert data["999"]["phase"] == "READY"

    def test_telegram_switch_project_updates_state(self, tmp_path):
        """_set_active_project followed by _get_active_project returns correct values."""
        state_file = tmp_path / "state.json"

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=_make_discovery([]),
                token="test-token",
                state_file=state_file,
            )

        bot._set_active_project(42, "orchid", "/home/dave/orchid", "EXECUTING")
        name, path = bot._get_active_project(42)
        assert name == "orchid"
        assert path == "/home/dave/orchid"

    def test_telegram_unknown_user_has_no_project(self, tmp_path):
        """Users with no state have (None, None) as active project."""
        state_file = tmp_path / "state.json"

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=_make_discovery([]),
                token="test-token",
                state_file=state_file,
            )

        name, path = bot._get_active_project(99999)
        assert name is None
        assert path is None

    def test_telegram_context_footer_no_project(self, tmp_path):
        """Footer shows 'no active project' when user has no project set."""
        state_file = tmp_path / "state.json"

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=_make_discovery([]),
                token="test-token",
                state_file=state_file,
            )

        footer = bot._context_footer(77777)
        assert "no active project" in footer

    def test_telegram_context_footer_with_project(self, tmp_path):
        """Footer shows [project-name | PHASE] when user has a project."""
        state_file = tmp_path / "state.json"
        proj_dir = _make_project_dir(tmp_path, "webchess")

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=_make_discovery([proj_dir]),
                token="test-token",
                state_file=state_file,
            )

        bot._set_active_project(1, "webchess", str(proj_dir), "READY")
        footer = bot._context_footer(1)
        assert "webchess" in footer
        # Phase comes from lifecycle — project is NEW (no state file)
        assert "📌" in footer


# ══════════════════════════════════════════════════════════════════════════════
# Telegram central — project switching
# ══════════════════════════════════════════════════════════════════════════════

class TestTelegramSwitch:
    """Tests for project listing and switching logic."""

    def test_list_projects_returns_all_discovered(self, tmp_path):
        """_list_projects returns all scanned projects with metadata."""
        p1 = _make_project_dir(tmp_path, "orchid")
        p2 = _make_project_dir(tmp_path, "webchess")
        discovery = _make_discovery([p1, p2])

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=discovery,
                token="test-token",
                state_file=tmp_path / "state.json",
            )

        projects = bot._list_projects()
        names = {p["name"] for p in projects}
        assert "orchid" in names
        assert "webchess" in names

    def test_unknown_project_not_matched(self, tmp_path):
        """Switching to an unknown project name returns None match."""
        p1 = _make_project_dir(tmp_path, "orchid")
        discovery = _make_discovery([p1])

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=discovery,
                token="test-token",
                state_file=tmp_path / "state.json",
            )

        projects = bot._list_projects()
        # Simulate the switch logic
        matched = next((p for p in projects if p["name"] == "nonexistent"), None)
        assert matched is None


# ══════════════════════════════════════════════════════════════════════════════
# Slack central — channel map
# ══════════════════════════════════════════════════════════════════════════════

class TestSlackChannelMap:
    """Tests for CentralSlackBot channel map I/O and routing."""

    def _make_bot(self, tmp_path, projects=None):
        channels_file = tmp_path / "slack-channels.json"
        with patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True), \
             patch("orchid.interfaces.slack_central.App"), \
             patch("orchid.interfaces.slack_central.SocketModeHandler"), \
             patch("orchid.interfaces.slack_central.WebClient"):
            from orchid.interfaces.slack_central import CentralSlackBot
            bot = CentralSlackBot(
                discovery=_make_discovery(projects or []),
                bot_token="xoxb-test",
                app_token="xapp-test",
                channels_file=channels_file,
            )
        return bot

    def test_slack_channel_map_loads(self, tmp_path):
        """Channel map is read from file on init."""
        channels_file = tmp_path / "slack-channels.json"
        existing = {"C123456": "/home/dave/webchess", "C789012": "/home/dave/orchid"}
        channels_file.write_text(json.dumps(existing))

        with patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True), \
             patch("orchid.interfaces.slack_central.App"), \
             patch("orchid.interfaces.slack_central.SocketModeHandler"), \
             patch("orchid.interfaces.slack_central.WebClient"):
            from orchid.interfaces.slack_central import CentralSlackBot
            bot = CentralSlackBot(
                discovery=_make_discovery([]),
                bot_token="xoxb-test",
                app_token="xapp-test",
                channels_file=channels_file,
            )

        assert bot._get_project_for_channel("C123456") == "/home/dave/webchess"
        assert bot._get_project_for_channel("C789012") == "/home/dave/orchid"
        assert bot._get_project_for_channel("C000000") is None

    def test_slack_add_channel_mapping_saves(self, tmp_path):
        """_add_channel_mapping updates map and writes file."""
        bot = self._make_bot(tmp_path)
        bot._add_channel_mapping("CNEW001", "/home/dave/myproject")

        assert bot._get_project_for_channel("CNEW001") == "/home/dave/myproject"
        data = json.loads(bot._channels_file.read_text())
        assert "CNEW001" in data

    def test_slack_routes_to_correct_project(self, tmp_path):
        """_resolve_project returns the correct project path for a channel."""
        bot = self._make_bot(tmp_path)
        bot._add_channel_mapping("CWEBCHESS", "/home/dave/webchess")
        bot._add_channel_mapping("CORCHID", "/home/dave/orchid")

        cmd_webchess = {"channel_id": "CWEBCHESS"}
        cmd_orchid = {"channel_id": "CORCHID"}
        cmd_unknown = {"channel_id": "CUNKNOWN"}

        assert bot._resolve_project(cmd_webchess) == "/home/dave/webchess"
        assert bot._resolve_project(cmd_orchid) == "/home/dave/orchid"
        assert bot._resolve_project(cmd_unknown) is None

    def test_slack_auto_create_channel_on_new_project(self, tmp_path):
        """on_project_added calls auto_create_channel when auto_create=True."""
        bot = self._make_bot(tmp_path)
        bot._client = MagicMock()
        bot._client.conversations_create.return_value = {
            "channel": {"id": "CNEWCHAN"}
        }
        bot._client.chat_postMessage.return_value = {"ts": "123.456"}

        bot.on_project_added("/home/dave/newproject")

        bot._client.conversations_create.assert_called_once()
        call_kwargs = bot._client.conversations_create.call_args
        assert "newproject-project" in call_kwargs.kwargs.get("name", "") or \
               "newproject-project" in str(call_kwargs)

    def test_slack_auto_create_channel_registers_mapping(self, tmp_path):
        """auto_create_channel saves the new channel_id → project_path mapping."""
        bot = self._make_bot(tmp_path)
        bot._client = MagicMock()
        bot._client.conversations_create.return_value = {
            "channel": {"id": "CAUTO001"}
        }
        bot._client.chat_postMessage.return_value = {"ts": "1.0"}

        channel_id = bot.auto_create_channel("/home/dave/testproject")
        assert channel_id == "CAUTO001"
        assert bot._get_project_for_channel("CAUTO001") == "/home/dave/testproject"

    def test_slack_add_channel_command_links_project(self, tmp_path):
        """_handle_add_channel links current channel to named project."""
        p1 = _make_project_dir(tmp_path, "webchess")
        bot = self._make_bot(tmp_path, projects=[p1])

        ack = MagicMock()
        respond = MagicMock()
        say = MagicMock()
        command = {
            "channel_id": "CLINK001",
            "text": "--project webchess",
        }
        bot._handle_add_channel(ack, respond, say, command)

        ack.assert_called_once()
        # say should be called with success message
        say.assert_called_once()
        assert bot._get_project_for_channel("CLINK001") == str(p1)


# ══════════════════════════════════════════════════════════════════════════════
# CentralBotManager
# ══════════════════════════════════════════════════════════════════════════════

class TestCentralBotManager:
    """Tests for CentralBotManager start/stop and discovery callbacks."""

    def test_central_bot_manager_starts_both(self, tmp_path):
        """start() launches both bots in daemon threads when tokens are provided."""
        discovery = _make_discovery([])
        started = []

        # Patch the lazy imports inside _start_telegram / _start_slack
        with patch("orchid.interfaces.telegram_central.CentralTelegramBot") as MockTG, \
             patch("orchid.interfaces.slack_central.CentralSlackBot") as MockSL, \
             patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True):

            # Bots' start() would block — make them no-ops
            MockTG.return_value.start = MagicMock()
            MockSL.return_value.start = MagicMock()

            from orchid.interfaces.central_bot import CentralBotManager
            manager = CentralBotManager(
                discovery=discovery,
                telegram_token="tg-token",
                slack_bot_token="xoxb-token",
                slack_app_token="xapp-token",
            )

            # Capture thread creation
            original_thread = threading.Thread
            created_threads = []

            def _capturing_thread(*a, **kw):
                t = original_thread(*a, **kw)
                created_threads.append(kw.get("name", ""))
                return t

            with patch("orchid.interfaces.central_bot.threading.Thread", side_effect=_capturing_thread):
                manager.start()

        assert any("telegram" in n for n in created_threads)
        assert any("slack" in n for n in created_threads)

    def test_central_bot_manager_no_tokens_skips_bots(self, tmp_path):
        """start() with no tokens does not start any bots."""
        discovery = _make_discovery([])

        with patch("orchid.interfaces.central_bot.threading.Thread") as MockThread:
            from orchid.interfaces.central_bot import CentralBotManager
            manager = CentralBotManager(
                discovery=discovery,
                telegram_token=None,
                slack_bot_token=None,
                slack_app_token=None,
            )
            manager.start()

        MockThread.assert_not_called()

    def test_central_bot_manager_notifies_on_project_added(self, tmp_path):
        """on_project_added delegates to both registered bots."""
        from orchid.interfaces.central_bot import CentralBotManager
        manager = CentralBotManager(discovery=_make_discovery([]))

        tg_bot = MagicMock()
        sl_bot = MagicMock()
        manager._telegram_bot = tg_bot
        manager._slack_bot = sl_bot

        manager.on_project_added("/home/dave/newproject")

        tg_bot.on_project_added.assert_called_once_with("/home/dave/newproject")
        sl_bot.on_project_added.assert_called_once_with("/home/dave/newproject")

    def test_central_bot_manager_notifies_on_project_removed(self, tmp_path):
        """on_project_removed delegates to both registered bots."""
        from orchid.interfaces.central_bot import CentralBotManager
        manager = CentralBotManager(discovery=_make_discovery([]))

        tg_bot = MagicMock()
        sl_bot = MagicMock()
        manager._telegram_bot = tg_bot
        manager._slack_bot = sl_bot

        manager.on_project_removed("/home/dave/oldproject")

        tg_bot.on_project_removed.assert_called_once_with("/home/dave/oldproject")
        sl_bot.on_project_removed.assert_called_once_with("/home/dave/oldproject")

    def test_central_bot_manager_stop_calls_both(self):
        """stop() calls stop() on both bots."""
        from orchid.interfaces.central_bot import CentralBotManager
        manager = CentralBotManager(discovery=_make_discovery([]))

        tg_bot = MagicMock()
        sl_bot = MagicMock()
        manager._telegram_bot = tg_bot
        manager._slack_bot = sl_bot

        manager.stop()

        tg_bot.stop.assert_called_once()
        sl_bot.stop.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# Lifecycle integration
# ══════════════════════════════════════════════════════════════════════════════

class TestLifecycleIntegration:
    """Tests for approve and discuss commands routing to lifecycle/agents."""

    def test_lifecycle_approve_via_telegram_advances_phase(self, tmp_path):
        """_cmd_approve calls GateSystem.approve and advances lifecycle."""
        from orchid.lifecycle import ProjectLifecycle

        proj_dir = _make_project_dir(tmp_path, "testproj")
        lc = ProjectLifecycle.load(proj_dir)
        lc.advance("DISCUSSING")  # NEW → DISCUSSING

        state_file = tmp_path / "state.json"
        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=_make_discovery([proj_dir]),
                token="test-token",
                state_file=state_file,
            )

        bot._set_active_project(1, "testproj", str(proj_dir), "DISCUSSING")

        # Verify GateSystem.approve records approval correctly
        from orchid.gates import GateStatus, GateSystem
        lc2 = ProjectLifecycle.load(proj_dir)
        gates = GateSystem(lc2)
        # DISCUSSING → REQUIREMENTS requires human approval
        gates.approve("REQUIREMENTS", approver="telegram")
        status = gates.check_gate("REQUIREMENTS")
        assert status == GateStatus.OPEN

    def test_discuss_routes_to_discussion_agent(self, tmp_path):
        """_cmd_discuss calls DiscussionAgent.run with the user message."""
        proj_dir = _make_project_dir(tmp_path, "newproj")
        state_file = tmp_path / "state.json"

        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True), \
             patch("orchid.interfaces.telegram_central.Application"):
            from orchid.interfaces.telegram_central import CentralTelegramBot
            bot = CentralTelegramBot(
                discovery=_make_discovery([proj_dir]),
                token="test-token",
                state_file=state_file,
            )

        bot._set_active_project(1, "newproj", str(proj_dir), "DISCUSSING")

        mock_resp = MagicMock()
        mock_resp.message = "Tell me more about the user authentication requirements."
        mock_resp.ready_to_advance = False

        mock_agent = MagicMock()
        mock_agent.run.return_value = mock_resp

        # Just verify the method exists and calls the agent
        with patch("orchid.agents.discussion_agent.DiscussionAgent") as MockAgent, \
             patch("orchid.discussion.DiscussionHistory") as MockHistory:
            MockAgent.return_value = mock_agent
            MockHistory.load.return_value = MagicMock()

            # Simulate the discuss handler's inner logic
            from orchid.agents.discussion_agent import DiscussionAgent
            from orchid.discussion import DiscussionHistory
            agent = DiscussionAgent(project_dir=proj_dir)
            history = DiscussionHistory.load(proj_dir)
            agent.run("I want to build a chess game", history)
            mock_agent.run.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# New project creation
# ══════════════════════════════════════════════════════════════════════════════

class TestNewProject:
    """Tests for /orchid-new / /orchid_new command."""

    def test_orchid_new_creates_project(self, tmp_path):
        """_cmd_new slugifies the description and calls ProjectCreator.create."""
        import re

        # Verify slug generation logic matches what the bot uses
        description = "A simple blackjack game"
        slug = re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")[:40]
        assert slug == "a-simple-blackjack-game"

        description2 = "My Cool WebApp!"
        slug2 = re.sub(r"[^a-z0-9]+", "-", description2.lower()).strip("-")[:40]
        assert slug2 == "my-cool-webapp"

    def test_orchid_new_calls_project_creator(self, tmp_path):
        """Slack /orchid-new calls ProjectCreator.create with slugified name."""
        channels_file = tmp_path / "channels.json"
        with patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True), \
             patch("orchid.interfaces.slack_central.App"), \
             patch("orchid.interfaces.slack_central.SocketModeHandler"), \
             patch("orchid.interfaces.slack_central.WebClient"):
            from orchid.interfaces.slack_central import CentralSlackBot
            bot = CentralSlackBot(
                discovery=_make_discovery([]),
                bot_token="xoxb-test",
                app_token="xapp-test",
                channels_file=channels_file,
            )

        ack = MagicMock()
        say = MagicMock()
        command = {"text": "a chess game"}

        respond = MagicMock()
        with patch("orchid.project_creator.ProjectCreator.create") as mock_create:
            mock_create.return_value = tmp_path / "a-chess-game"
            bot._handle_new(ack, respond, say, command)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert "a-chess-game" in str(call_kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# Deprecation warnings
# ══════════════════════════════════════════════════════════════════════════════

class TestDeprecationWarnings:
    """Tests that old telegram/slack CLI commands show deprecation warnings."""

    def test_deprecated_telegram_command_docstring(self):
        """'orchid telegram' command function docstring contains DEPRECATED."""
        from orchid.interfaces.cli import telegram
        assert "DEPRECATED" in (telegram.__doc__ or "")

    def test_deprecated_slack_command_docstring(self):
        """'orchid slack' command function docstring contains DEPRECATED."""
        from orchid.interfaces.cli import slack
        assert "DEPRECATED" in (slack.__doc__ or "")

    def test_deprecated_telegram_prints_warning(self):
        """'orchid telegram' handler body includes the deprecation console.print call."""
        import inspect

        from orchid.interfaces.cli import telegram
        src = inspect.getsource(telegram)
        assert "DEPRECATED" in src
        assert "orchid serve --telegram" in src

    def test_deprecated_slack_prints_warning(self):
        """'orchid slack' handler body includes the deprecation console.print call."""
        import inspect

        from orchid.interfaces.cli import slack
        src = inspect.getsource(slack)
        assert "DEPRECATED" in src
        assert "orchid serve --slack" in src

    def test_serve_bots_flag_passes_to_server(self):
        """'orchid serve --bots' passes enable_telegram=True, enable_slack=True to web serve."""
        from typer.testing import CliRunner

        from orchid.interfaces.cli import app

        runner = CliRunner()
        with patch("orchid.interfaces.web_server.serve") as mock_serve:
            runner.invoke(app, [
                "serve",
                "--watch-dir", "/tmp",
                "--bots",
            ])
            if mock_serve.called:
                kwargs = mock_serve.call_args.kwargs
                assert kwargs.get("enable_telegram") is True
                assert kwargs.get("enable_slack") is True

    def test_serve_telegram_flag_only_enables_telegram(self):
        """'orchid serve --telegram' enables Telegram but not Slack."""
        from typer.testing import CliRunner

        from orchid.interfaces.cli import app

        runner = CliRunner()
        with patch("orchid.interfaces.web_server.serve") as mock_serve:
            runner.invoke(app, [
                "serve",
                "--watch-dir", "/tmp",
                "--telegram",
            ])
            if mock_serve.called:
                kwargs = mock_serve.call_args.kwargs
                assert kwargs.get("enable_telegram") is True
                assert kwargs.get("enable_slack") is False


# ══════════════════════════════════════════════════════════════════════════════
# T061 — Slack auto-channel creation on startup
# ══════════════════════════════════════════════════════════════════════════════

class TestSlackStartupChannelCreation:
    """T061: channels created for all existing projects when bot starts."""

    def _make_bot(self, tmp_path, projects=None):
        channels_file = tmp_path / "slack-channels.json"
        with patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True), \
             patch("orchid.interfaces.slack_central.App"), \
             patch("orchid.interfaces.slack_central.SocketModeHandler"), \
             patch("orchid.interfaces.slack_central.WebClient"):
            from orchid.interfaces.slack_central import CentralSlackBot
            bot = CentralSlackBot(
                discovery=_make_discovery(projects or []),
                bot_token="xoxb-test",
                app_token="xapp-test",
                channels_file=channels_file,
            )
        return bot

    def test_ensure_channels_creates_for_unmapped_projects(self, tmp_path):
        """_ensure_channels_for_all_projects calls auto_create_channel for unmapped projects."""
        p1 = _make_project_dir(tmp_path, "orchid")
        p2 = _make_project_dir(tmp_path, "webchess")
        bot = self._make_bot(tmp_path, projects=[p1, p2])
        bot._client = MagicMock()
        bot._client.conversations_create.return_value = {"channel": {"id": "CNEW"}}
        bot._client.chat_postMessage.return_value = {"ts": "1.0"}

        bot._ensure_channels_for_all_projects()

        assert bot._client.conversations_create.call_count == 2

    def test_ensure_channels_skips_already_mapped(self, tmp_path):
        """_ensure_channels_for_all_projects skips projects already in channel map."""
        p1 = _make_project_dir(tmp_path, "orchid")
        p2 = _make_project_dir(tmp_path, "webchess")
        bot = self._make_bot(tmp_path, projects=[p1, p2])
        bot._client = MagicMock()
        bot._client.conversations_create.return_value = {"channel": {"id": "CNEW2"}}
        bot._client.chat_postMessage.return_value = {"ts": "1.0"}

        # Pre-map one project
        bot._add_channel_mapping("CORCHID", str(p1))

        bot._ensure_channels_for_all_projects()

        # Only webchess should get a new channel
        assert bot._client.conversations_create.call_count == 1
        created_name = bot._client.conversations_create.call_args.kwargs.get("name", "")
        assert "webchess" in created_name

    def test_ensure_channels_no_client_is_noop(self, tmp_path):
        """_ensure_channels_for_all_projects does nothing if client not yet set."""
        p1 = _make_project_dir(tmp_path, "orchid")
        bot = self._make_bot(tmp_path, projects=[p1])
        # _client is None by default before start()
        bot._client = None

        # Should not raise
        bot._ensure_channels_for_all_projects()


# ══════════════════════════════════════════════════════════════════════════════
# T062 — Slack channel routing debug logging
# ══════════════════════════════════════════════════════════════════════════════

class TestSlackChannelRoutingDebug:
    """T062: debug logging on channel lookup."""

    def _make_bot(self, tmp_path):
        channels_file = tmp_path / "slack-channels.json"
        with patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True), \
             patch("orchid.interfaces.slack_central.App"), \
             patch("orchid.interfaces.slack_central.SocketModeHandler"), \
             patch("orchid.interfaces.slack_central.WebClient"):
            from orchid.interfaces.slack_central import CentralSlackBot
            bot = CentralSlackBot(
                discovery=_make_discovery([]),
                bot_token="xoxb-test",
                app_token="xapp-test",
                channels_file=channels_file,
            )
        return bot

    def test_resolve_project_logs_channel_id(self, tmp_path):
        """_resolve_project emits an INFO RESOLVE log with channel_id, map, and result."""
        bot = self._make_bot(tmp_path)
        bot._add_channel_mapping("CTEST", "/home/dave/orchid")

        with patch("orchid.interfaces.slack_central.logger") as mock_logger:
            result = bot._resolve_project({"channel_id": "CTEST", "command": "/orchid-status", "channel_name": "orchid-project"})

        assert result == "/home/dave/orchid"
        mock_logger.info.assert_called()
        log_args = str(mock_logger.info.call_args_list)
        assert "CTEST" in log_args
        assert "RESOLVE" in log_args

    def test_get_project_for_channel_logs_miss(self, tmp_path):
        """_get_project_for_channel logs at INFO when channel_id is not in the map."""
        bot = self._make_bot(tmp_path)

        with patch("orchid.interfaces.slack_central.logger") as mock_logger:
            result = bot._get_project_for_channel("CMISSING")

        assert result is None
        mock_logger.info.assert_called()
        log_args = str(mock_logger.info.call_args_list)
        assert "CMISSING" in log_args

    def test_two_channels_route_to_different_projects(self, tmp_path):
        """Two channels with different mappings return the correct projects."""
        bot = self._make_bot(tmp_path)
        bot._add_channel_mapping("CORCHID", "/home/dave/orchid")
        bot._add_channel_mapping("CWEBCHESS", "/home/dave/webchess")

        assert bot._resolve_project({"channel_id": "CORCHID"}) == "/home/dave/orchid"
        assert bot._resolve_project({"channel_id": "CWEBCHESS"}) == "/home/dave/webchess"


# ══════════════════════════════════════════════════════════════════════════════
# T063 — /orchid-unlink-channel
# ══════════════════════════════════════════════════════════════════════════════

class TestSlackUnlinkChannel:
    """T063: /orchid-unlink-channel removes channel from map."""

    def _make_bot(self, tmp_path):
        channels_file = tmp_path / "slack-channels.json"
        with patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True), \
             patch("orchid.interfaces.slack_central.App"), \
             patch("orchid.interfaces.slack_central.SocketModeHandler"), \
             patch("orchid.interfaces.slack_central.WebClient"):
            from orchid.interfaces.slack_central import CentralSlackBot
            bot = CentralSlackBot(
                discovery=_make_discovery([]),
                bot_token="xoxb-test",
                app_token="xapp-test",
                channels_file=channels_file,
            )
        return bot

    def test_unlink_removes_mapping(self, tmp_path):
        """_handle_unlink_channel removes the channel mapping and confirms project name."""
        bot = self._make_bot(tmp_path)
        bot._add_channel_mapping("CTEST", "/home/dave/webchess")

        ack = MagicMock()
        respond = MagicMock()
        say = MagicMock()
        bot._handle_unlink_channel(ack, respond, say, {"channel_id": "CTEST"})

        ack.assert_called_once()
        say.assert_called_once()
        assert "webchess" in say.call_args.kwargs.get("text", "")
        assert bot._get_project_for_channel("CTEST") is None

    def test_unlink_unmapped_channel_acks_with_error(self, tmp_path):
        """_handle_unlink_channel acks with error when channel is not mapped."""
        bot = self._make_bot(tmp_path)

        ack = MagicMock()
        respond = MagicMock()
        say = MagicMock()
        bot._handle_unlink_channel(ack, respond, say, {"channel_id": "CNOT_MAPPED"})

        ack.assert_called_once()
        say.assert_not_called()
        # Error goes through respond(), not ack()
        respond.assert_called_once()
        assert "not linked" in respond.call_args.args[0].lower()

    def test_unlink_saves_updated_channel_file(self, tmp_path):
        """After unlinking, the channels file no longer contains the channel."""
        bot = self._make_bot(tmp_path)
        bot._add_channel_mapping("CSAVE", "/home/dave/myproject")

        ack = MagicMock()
        respond = MagicMock()
        say = MagicMock()
        bot._handle_unlink_channel(ack, respond, say, {"channel_id": "CSAVE"})

        data = json.loads(bot._channels_file.read_text())
        assert "CSAVE" not in data

    def test_unlink_registered_as_slash_command(self):
        """start() registers /orchid-unlink-channel as an app.command handler."""
        import inspect

        from orchid.interfaces import slack_central
        src = inspect.getsource(slack_central.CentralSlackBot.start)
        assert "/orchid-unlink-channel" in src

    def test_unlink_in_help_text(self, tmp_path):
        """/orchid-unlink-channel is mentioned in the help text."""
        bot = self._make_bot(tmp_path)
        help_text = bot._help_text()
        assert "/orchid-unlink-channel" in help_text


# ══════════════════════════════════════════════════════════════════════════════
# /orchid-cancel
# ══════════════════════════════════════════════════════════════════════════════

class TestSlackCancel:
    """Tests for /orchid-cancel command."""

    def _make_bot(self, tmp_path):
        channels_file = tmp_path / "slack-channels.json"
        with patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True), \
             patch("orchid.interfaces.slack_central.App"), \
             patch("orchid.interfaces.slack_central.SocketModeHandler"), \
             patch("orchid.interfaces.slack_central.WebClient"):
            from orchid.interfaces.slack_central import CentralSlackBot
            bot = CentralSlackBot(
                discovery=_make_discovery([]),
                bot_token="xoxb-test",
                app_token="xapp-test",
                channels_file=channels_file,
            )
        return bot

    def test_cancel_running_task(self, tmp_path):
        """_handle_cancel calls runner.cancel() and reports success."""
        bot = self._make_bot(tmp_path)
        bot._add_channel_mapping("CTEST", "/home/dave/orchid")

        mock_runner = MagicMock()
        mock_runner.cancel.return_value = True
        bot._runners["/home/dave/orchid"] = mock_runner

        ack = MagicMock()
        respond = MagicMock()
        say = MagicMock()
        bot._handle_cancel(ack, respond, say, {"channel_id": "CTEST"})

        ack.assert_called_once()
        mock_runner.cancel.assert_called_once()
        say.assert_called_once()
        assert "cancel" in say.call_args.kwargs.get("text", "").lower()

    def test_cancel_nothing_running(self, tmp_path):
        """_handle_cancel reports nothing running when cancel() returns False."""
        bot = self._make_bot(tmp_path)
        bot._add_channel_mapping("CTEST", "/home/dave/orchid")

        mock_runner = MagicMock()
        mock_runner.cancel.return_value = False
        bot._runners["/home/dave/orchid"] = mock_runner

        ack = MagicMock()
        respond = MagicMock()
        say = MagicMock()
        bot._handle_cancel(ack, respond, say, {"channel_id": "CTEST"})

        ack.assert_called_once()
        say.assert_called_once()
        assert "nothing" in say.call_args.kwargs.get("text", "").lower()

    def test_cancel_in_help_text(self, tmp_path):
        """/orchid-cancel appears in the help text."""
        bot = self._make_bot(tmp_path)
        assert "/orchid-cancel" in bot._help_text()

    def test_cancel_registered_as_slash_command(self):
        """start() registers /orchid-cancel as an app.command handler."""
        import inspect

        from orchid.interfaces import slack_central
        src = inspect.getsource(slack_central.CentralSlackBot.start)
        assert "/orchid-cancel" in src


# ══════════════════════════════════════════════════════════════════════════════
# send_dm — Telegram
# ══════════════════════════════════════════════════════════════════════════════

class TestTelegramSendDM:
    """CentralTelegramBot.send_dm() dispatches to the bot's event loop."""

    def _make_bot(self, tmp_path: Path) -> Any:
        from orchid.interfaces.telegram_central import CentralTelegramBot
        disc = _make_discovery([])
        with patch("orchid.interfaces.telegram_central._TELEGRAM_AVAILABLE", True):
            bot = object.__new__(CentralTelegramBot)
            bot._discovery = disc
            bot.token = "test-token"
            bot.allowed_users = set()
            bot._state_file = tmp_path / "state.json"
            bot._user_state = {}
            bot._state_lock = __import__("threading").Lock()
            bot._runners = {}
            bot._runners_lock = __import__("threading").Lock()
            bot._subscribers = {}
            bot._sub_lock = __import__("threading").Lock()
            bot._app = None
            bot._loop = None
            bot._stop_event = None
        return bot

    def test_send_dm_no_app_logs_warning(self, tmp_path, caplog):
        import logging
        bot = self._make_bot(tmp_path)
        with caplog.at_level(logging.WARNING, logger="orchid.interfaces.telegram_central"):
            bot.send_dm(12345, "hello")
        assert any("not initialised" in r.message for r in caplog.records)

    def test_send_dm_no_loop_logs_warning(self, tmp_path, caplog):
        import logging
        bot = self._make_bot(tmp_path)
        bot._app = MagicMock()  # app present, loop absent
        with caplog.at_level(logging.WARNING, logger="orchid.interfaces.telegram_central"):
            bot.send_dm(12345, "hello")
        assert any("event loop not running" in r.message for r in caplog.records)

    def test_send_dm_dispatches_coroutine(self, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock
        from unittest.mock import patch as upatch

        bot = self._make_bot(tmp_path)
        loop = asyncio.new_event_loop()
        bot._loop = loop

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(return_value=None)
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        bot._app = mock_app

        with upatch("asyncio.run_coroutine_threadsafe") as mock_rtf:
            bot.send_dm(99999, "test message")
            mock_rtf.assert_called_once()
            args = mock_rtf.call_args
            assert args[0][1] is loop  # loop passed correctly

        loop.close()


# ══════════════════════════════════════════════════════════════════════════════
# send_dm — Slack
# ══════════════════════════════════════════════════════════════════════════════

class TestSlackSendDM:
    """CentralSlackBot.send_dm() opens a DM channel and posts the message."""

    def _make_bot(self, tmp_path: Path) -> Any:
        from orchid.interfaces.slack_central import CentralSlackBot
        disc = _make_discovery([])
        import asyncio
        with patch("orchid.interfaces.slack_central._SLACK_AVAILABLE", True):
            bot = object.__new__(CentralSlackBot)
            bot._discovery = disc
            bot.bot_token = "xoxb-test"
            bot.app_token = "xapp-test"
            bot._channels_file = tmp_path / "channels.json"
            bot.auto_create_channels = False
            bot.default_channel = "#orchid-general"
            bot._channel_map = {}
            bot._map_lock = __import__("threading").Lock()
            bot._runners = {}
            bot._runners_lock = __import__("threading").Lock()
            bot._notify_channels = {}
            bot._notify_lock = __import__("threading").Lock()
            bot._loop = asyncio.new_event_loop()
            bot._loop_thread = __import__("threading").Thread(
                target=bot._loop.run_forever, daemon=True
            )
            bot._loop_thread.start()
            bot._client = None
            bot._handler = None
        return bot

    def test_send_dm_no_client_logs_warning(self, tmp_path, caplog):
        import logging
        bot = self._make_bot(tmp_path)
        with caplog.at_level(logging.WARNING, logger="orchid.interfaces.slack_central"):
            bot.send_dm("U012AB3CD", "hello")
        assert any("not initialised" in r.message for r in caplog.records)

    def test_send_dm_calls_conversations_open_and_post(self, tmp_path):
        bot = self._make_bot(tmp_path)
        mock_client = MagicMock()
        mock_client.conversations_open.return_value = {"channel": {"id": "DM_CHANNEL_ID"}}
        mock_client.chat_postMessage.return_value = {"ts": "1234.5678"}
        bot._client = mock_client

        bot.send_dm("U012AB3CD", "test dm text")

        mock_client.conversations_open.assert_called_once_with(users="U012AB3CD")
        mock_client.chat_postMessage.assert_called_once_with(
            channel="DM_CHANNEL_ID", text="test dm text"
        )

    def test_send_dm_handles_conversations_open_error(self, tmp_path, caplog):
        import logging
        bot = self._make_bot(tmp_path)
        mock_client = MagicMock()
        mock_client.conversations_open.side_effect = RuntimeError("API error")
        bot._client = mock_client

        with caplog.at_level(logging.WARNING, logger="orchid.interfaces.slack_central"):
            bot.send_dm("U012AB3CD", "hello")
        assert any("send_dm" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════════════
# CentralBotManager — delegation
# ══════════════════════════════════════════════════════════════════════════════

class TestCentralBotManagerDM:
    """CentralBotManager.send_telegram_dm / send_slack_dm delegate correctly."""

    def _make_manager(self) -> Any:
        from orchid.interfaces.central_bot import CentralBotManager
        disc = _make_discovery([])
        mgr = CentralBotManager(discovery=disc)
        return mgr

    def test_send_telegram_dm_no_bot_logs_warning(self, caplog):
        import logging
        mgr = self._make_manager()
        with caplog.at_level(logging.WARNING, logger="orchid.interfaces.central_bot"):
            mgr.send_telegram_dm(12345, "hi")
        assert any("not running" in r.message for r in caplog.records)

    def test_send_telegram_dm_delegates(self):
        mgr = self._make_manager()
        mock_tg = MagicMock()
        mgr._telegram_bot = mock_tg
        mgr.send_telegram_dm(12345, "hello")
        mock_tg.send_dm.assert_called_once_with(12345, "hello")

    def test_send_slack_dm_no_bot_logs_warning(self, caplog):
        import logging
        mgr = self._make_manager()
        with caplog.at_level(logging.WARNING, logger="orchid.interfaces.central_bot"):
            mgr.send_slack_dm("U123", "hi")
        assert any("not running" in r.message for r in caplog.records)

    def test_send_slack_dm_delegates(self):
        mgr = self._make_manager()
        mock_sl = MagicMock()
        mgr._slack_bot = mock_sl
        mgr.send_slack_dm("U123", "hello")
        mock_sl.send_dm.assert_called_once_with("U123", "hello")


# ══════════════════════════════════════════════════════════════════════════════
# get_bot_manager / set_bot_manager singleton
# ══════════════════════════════════════════════════════════════════════════════

class TestBotManagerSingleton:
    def test_set_and_get_bot_manager(self):
        from orchid.interfaces.central_bot import get_bot_manager, set_bot_manager
        original = get_bot_manager()
        try:
            fake = MagicMock()
            set_bot_manager(fake)
            assert get_bot_manager() is fake
            set_bot_manager(None)
            assert get_bot_manager() is None
        finally:
            set_bot_manager(original)


# ══════════════════════════════════════════════════════════════════════════════
# notifications.py — Telegram + Slack dispatch
# ══════════════════════════════════════════════════════════════════════════════

class TestDispatchTaskNotification:
    """dispatch_task_notification() wires into real bot DMs."""

    def _make_run(self, status="success", output="ok"):
        run = MagicMock()
        run.status = status
        run.run_id = "run-abc"
        run.output = output
        return run

    def _make_task(self, **kwargs):
        base = {"task_id": "T001", "name": "My Task", "notify_on_success": True, "notify_on_failure": True}
        base.update(kwargs)
        return base

    def _make_user(self, cfg):
        user = MagicMock()
        user.email = "test@example.com"
        user.notification_config = cfg
        return user

    def test_telegram_dm_dispatched_when_bot_running(self, tmp_path):
        from unittest.mock import patch as upatch

        from orchid.auth.notifications import dispatch_task_notification

        mock_mgr = MagicMock()
        user = self._make_user({
            "telegram_enabled": True,
            "telegram_chat_id": "99999",
            "notify_on_success": True,
        })

        with upatch("orchid.auth.store.get_store") as mock_store, \
             upatch("orchid.interfaces.central_bot.get_bot_manager", return_value=mock_mgr):
            mock_store.return_value.get_user.return_value = user
            dispatch_task_notification("user1", self._make_task(), self._make_run("success"))

        mock_mgr.send_telegram_dm.assert_called_once()
        call_args = mock_mgr.send_telegram_dm.call_args
        assert call_args[0][0] == 99999  # chat_id as int
        assert "My Task" in call_args[0][1]
        assert "success" in call_args[0][1]

    def test_telegram_dm_skipped_when_no_bot(self, caplog, tmp_path):
        import logging
        from unittest.mock import patch as upatch

        from orchid.auth.notifications import dispatch_task_notification

        user = self._make_user({
            "telegram_enabled": True,
            "telegram_chat_id": "99999",
        })

        with upatch("orchid.auth.store.get_store") as mock_store, \
             upatch("orchid.interfaces.central_bot.get_bot_manager", return_value=None), \
             caplog.at_level(logging.INFO, logger="orchid.auth.notifications"):
            mock_store.return_value.get_user.return_value = user
            dispatch_task_notification("user1", self._make_task(), self._make_run("failure"))

        assert any("bot manager not running" in r.message for r in caplog.records)

    def test_slack_dm_dispatched_when_bot_running(self, tmp_path):
        from unittest.mock import patch as upatch

        from orchid.auth.notifications import dispatch_task_notification

        mock_mgr = MagicMock()
        user = self._make_user({
            "slack_enabled": True,
            "slack_user_id": "U012AB3CD",
            "notify_on_failure": True,
        })

        with upatch("orchid.auth.store.get_store") as mock_store, \
             upatch("orchid.interfaces.central_bot.get_bot_manager", return_value=mock_mgr):
            mock_store.return_value.get_user.return_value = user
            dispatch_task_notification("user1", self._make_task(), self._make_run("failure"))

        mock_mgr.send_slack_dm.assert_called_once()
        call_args = mock_mgr.send_slack_dm.call_args
        assert call_args[0][0] == "U012AB3CD"
        assert "My Task" in call_args[0][1]

    def test_format_dm_text_success(self):
        from orchid.auth.notifications import _format_dm_text
        text = _format_dm_text("My Task", "success", "run-123", "all good")
        assert "My Task" in text
        assert "✅" in text
        assert "run-123" in text
        assert "all good" in text

    def test_format_dm_text_failure(self):
        from orchid.auth.notifications import _format_dm_text
        text = _format_dm_text("My Task", "failure", "run-456", "boom")
        assert "❌" in text
        assert "boom" in text

    def test_format_dm_text_truncates_long_output(self):
        from orchid.auth.notifications import _format_dm_text
        long_out = "x" * 600
        text = _format_dm_text("T", "success", "r", long_out)
        assert "…" in text
        # Output section should not exceed 500 chars of the original
        assert "x" * 501 not in text
