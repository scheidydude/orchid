"""CentralBotManager — unified coordinator for Telegram and Slack central bots.

Starts both bots in daemon threads, wires discovery callbacks to each bot,
and provides a clean start/stop interface for orchid serve.

Architecture (D0050):
- Both bots share the same ProjectDiscovery instance
- on_project_added / on_project_removed called by web_server._lifespan
- Each bot runs in its own daemon thread (blocking start() call)
- Config sourced from bots.telegram / bots.slack in orchid.defaults.yaml
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CentralBotManager:
    """Manages the central Telegram and Slack bots as a unified service."""

    def __init__(
        self,
        discovery: Any,
        telegram_token: str | None = None,
        telegram_allowed_users: list[int] | None = None,
        telegram_state_file: Path | None = None,
        slack_bot_token: str | None = None,
        slack_app_token: str | None = None,
        slack_channels_file: Path | None = None,
        slack_auto_create_channels: bool = True,
    ) -> None:
        self._discovery = discovery
        self._telegram_token = telegram_token
        self._telegram_allowed_users = telegram_allowed_users or []
        self._telegram_state_file = telegram_state_file
        self._slack_bot_token = slack_bot_token
        self._slack_app_token = slack_app_token
        self._slack_channels_file = slack_channels_file
        self._slack_auto_create_channels = slack_auto_create_channels

        self._telegram_bot: Any | None = None
        self._slack_bot: Any | None = None
        self._telegram_thread: threading.Thread | None = None
        self._slack_thread: threading.Thread | None = None

    # ── Start / stop ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start enabled bots in daemon threads (non-blocking)."""
        if self._telegram_token:
            self._start_telegram()
        if self._slack_bot_token and self._slack_app_token:
            self._start_slack()

    def stop(self) -> None:
        """Gracefully stop all running bots."""
        if self._telegram_bot:
            try:
                self._telegram_bot.stop()
            except Exception as exc:
                logger.warning("Error stopping Telegram bot: %s", exc)
        if self._slack_bot:
            try:
                self._slack_bot.stop()
            except Exception as exc:
                logger.warning("Error stopping Slack bot: %s", exc)

    # ── Discovery callbacks ───────────────────────────────────────────────────

    def on_project_added(self, project_path: str) -> None:
        """Called when discovery finds a new project."""
        if self._telegram_bot:
            try:
                self._telegram_bot.on_project_added(project_path)
            except Exception as exc:
                logger.warning("Telegram on_project_added error: %s", exc)
        if self._slack_bot:
            try:
                self._slack_bot.on_project_added(project_path)
            except Exception as exc:
                logger.warning("Slack on_project_added error: %s", exc)

    def on_project_removed(self, project_path: str) -> None:
        """Called when discovery loses a project."""
        if self._telegram_bot:
            try:
                self._telegram_bot.on_project_removed(project_path)
            except Exception as exc:
                logger.warning("Telegram on_project_removed error: %s", exc)
        if self._slack_bot:
            try:
                self._slack_bot.on_project_removed(project_path)
            except Exception as exc:
                logger.warning("Slack on_project_removed error: %s", exc)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _start_telegram(self) -> None:
        try:
            from orchid.interfaces.telegram_central import CentralTelegramBot
        except ImportError as exc:
            logger.warning("Cannot start Telegram bot: %s", exc)
            return

        try:
            self._telegram_bot = CentralTelegramBot(
                discovery=self._discovery,
                token=self._telegram_token,
                allowed_users=self._telegram_allowed_users,
                state_file=self._telegram_state_file,
            )
        except ImportError as exc:
            logger.warning("Telegram bot unavailable (missing deps): %s", exc)
            return

        def _run() -> None:
            try:
                self._telegram_bot.start()
            except Exception as exc:
                logger.exception("Telegram bot crashed: %s", exc)

        self._telegram_thread = threading.Thread(
            target=_run, daemon=True, name="orchid-telegram-central"
        )
        self._telegram_thread.start()
        logger.info("Central Telegram bot started in background thread")

    def _start_slack(self) -> None:
        try:
            from orchid.interfaces.slack_central import CentralSlackBot
        except ImportError as exc:
            logger.warning("Cannot start Slack bot: %s", exc)
            return

        try:
            self._slack_bot = CentralSlackBot(
                discovery=self._discovery,
                bot_token=self._slack_bot_token,
                app_token=self._slack_app_token,
                channels_file=self._slack_channels_file,
                auto_create_channels=self._slack_auto_create_channels,
            )
        except ImportError as exc:
            logger.warning("Slack bot unavailable (missing deps): %s", exc)
            return

        def _run() -> None:
            try:
                self._slack_bot.start()
            except Exception as exc:
                logger.exception("Slack bot crashed: %s", exc)

        self._slack_thread = threading.Thread(
            target=_run, daemon=True, name="orchid-slack-central"
        )
        self._slack_thread.start()
        logger.info("Central Slack bot started in background thread")

    # ── Factory from config ───────────────────────────────────────────────────

    @classmethod
    def from_env(cls, discovery: Any) -> "CentralBotManager":
        """Build a CentralBotManager from environment variables and config."""
        from orchid import config as cfg

        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "") or cfg.get("bots.telegram.token", "")
        raw_users = os.environ.get("TELEGRAM_ALLOWED_USERS", "") or ""
        allowed_users = [
            int(u.strip()) for u in raw_users.split(",")
            if u.strip().isdigit()
        ]
        telegram_state_file_str = cfg.get(
            "bots.telegram.state_file",
            "~/.config/orchid/telegram-state.json"
        )

        slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "") or cfg.get("bots.slack.bot_token", "")
        slack_app_token = os.environ.get("SLACK_APP_TOKEN", "") or cfg.get("bots.slack.app_token", "")
        slack_channels_file_str = cfg.get(
            "bots.slack.channels_file",
            "~/.config/orchid/slack-channels.json"
        )
        slack_auto_create = cfg.get("bots.slack.auto_create_channels", True)

        return cls(
            discovery=discovery,
            telegram_token=telegram_token or None,
            telegram_allowed_users=allowed_users,
            telegram_state_file=Path(telegram_state_file_str).expanduser() if telegram_state_file_str else None,
            slack_bot_token=slack_bot_token or None,
            slack_app_token=slack_app_token or None,
            slack_channels_file=Path(slack_channels_file_str).expanduser() if slack_channels_file_str else None,
            slack_auto_create_channels=slack_auto_create,
        )
