"""Central Slack bot for Orchid — channel-per-project routing.

Replaces project-scoped SlackBot with a single central bot that routes
commands to the correct project based on which Slack channel the command
was sent from.

Architecture (D0050, D0052):
- Channel map persisted at ~/.config/orchid/slack-channels.json
  {channel_id: project_path}
- Auto-creates #<name>-project channel when new project is discovered
- Global commands in #orchid-general or DMs (no project context needed)
- Project commands auto-routed based on channel
- Socket Mode (D0024) — no public URL required

Usage:
    bot = CentralSlackBot(
        discovery=discovery,
        bot_token="xoxb-...",
        app_token="xapp-...",
    )
    bot.start()   # blocks
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CHANNELS_FILE = Path("~/.config/orchid/slack-channels.json").expanduser()

try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from slack_sdk import WebClient
    _SLACK_AVAILABLE = True
except ImportError:
    _SLACK_AVAILABLE = False


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class CentralSlackBot:
    """Central Orchid Slack bot — channel-per-project command routing."""

    def __init__(
        self,
        discovery: Any,
        bot_token: str,
        app_token: str,
        channels_file: Path | None = None,
        auto_create_channels: bool = True,
        default_channel: str = "#orchid-general",
    ) -> None:
        if not _SLACK_AVAILABLE:
            raise ImportError(
                "slack-bolt is not installed. "
                "Run: uv pip install 'slack-bolt>=1.18.0'"
            )
        self._discovery = discovery
        self.bot_token = bot_token
        self.app_token = app_token
        self._channels_file = channels_file or _CHANNELS_FILE
        self.auto_create_channels = auto_create_channels
        self.default_channel = default_channel

        # channel_id → project_path
        self._channel_map: dict[str, str] = {}
        self._map_lock = threading.Lock()

        # project_path → BackgroundRunner (lazy)
        self._runners: dict[str, Any] = {}
        self._runners_lock = threading.Lock()

        # channel_id → set[str] (notification targets)
        self._notify_channels: dict[str, set[str]] = {}
        self._notify_lock = threading.Lock()

        # Dedicated asyncio loop for BackgroundRunner async callbacks
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="orchid-slack-central-loop",
        )
        self._loop_thread.start()

        self._client: Any = None
        self._handler: Any = None

        self._load_channels()

    # ── Channel map I/O ───────────────────────────────────────────────────────

    def _load_channels(self) -> None:
        if self._channels_file.exists():
            try:
                data = json.loads(self._channels_file.read_text(encoding="utf-8"))
                with self._map_lock:
                    self._channel_map = data
                logger.info("Loaded %d entries from disk: %s", len(data), list(data.keys()))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load slack channels: %s", exc)
        else:
            logger.info("Channel map file does not exist yet: %s", self._channels_file)

    def _save_channels(self) -> None:
        with self._map_lock:
            data = dict(self._channel_map)
        try:
            _atomic_write_json(self._channels_file, data)
        except Exception as exc:
            logger.warning("Failed to save slack channels: %s", exc)

    def _get_project_for_channel(self, channel_id: str) -> str | None:
        with self._map_lock:
            result = self._channel_map.get(channel_id)
        logger.info("channel lookup: %r → %s", channel_id, result or "NOT FOUND")
        return result

    def _add_channel_mapping(self, channel_id: str, project_path: str) -> None:
        with self._map_lock:
            self._channel_map[channel_id] = project_path
        self._save_channels()

    def _remove_channel_mapping(self, channel_id: str) -> None:
        with self._map_lock:
            self._channel_map.pop(channel_id, None)
        self._save_channels()

    # ── Auto-channel creation ─────────────────────────────────────────────────

    def auto_create_channel(self, project_path: str) -> str | None:
        """Create a Slack channel for a project. Returns channel_id or None."""
        if not self._client or not self.auto_create_channels:
            return None
        project_name = Path(project_path).name
        channel_name = f"{project_name}-project"
        # Slack channel names: lowercase, max 80 chars, no special chars except - _
        channel_name = re.sub(r"[^a-z0-9_-]", "-", channel_name.lower())[:80]
        try:
            resp = self._client.conversations_create(name=channel_name, is_private=False)
            channel_id = resp["channel"]["id"]
            self._add_channel_mapping(channel_id, project_path)
            self._post(channel_id, f"🌸 New project registered: *{project_name}*\nThis channel is linked to `{project_path}`.")
            logger.info("Created Slack channel %s for project %s", channel_name, project_name)
            return channel_id
        except Exception as exc:
            logger.warning("Failed to create Slack channel %s: %s", channel_name, exc)
            return None

    def _ensure_channels_for_all_projects(self) -> None:
        """Create channels for any discovered projects not yet in the channel map.

        Called once during start() so that projects that existed before the bot
        started get their own channel, not just newly discovered ones.
        """
        with self._map_lock:
            mapped_paths = set(self._channel_map.values())
        for proj_path in self._discovery.scan():
            path_str = str(proj_path)
            if path_str not in mapped_paths:
                logger.info("Creating channel for existing project: %s", path_str)
                self.auto_create_channel(path_str)

    # ── Project discovery hooks ───────────────────────────────────────────────

    def on_project_added(self, project_path: str) -> None:
        """Called by CentralBotManager when discovery finds a new project."""
        logger.info("CentralSlackBot: project added %s", project_path)
        if self.auto_create_channels:
            self.auto_create_channel(project_path)

    def on_project_removed(self, project_path: str) -> None:
        logger.info("CentralSlackBot: project removed %s", project_path)
        with self._runners_lock:
            runner = self._runners.pop(project_path, None)
        if runner:
            runner.shutdown()
        # Remove channel mappings for this project
        with self._map_lock:
            to_remove = [cid for cid, p in self._channel_map.items() if p == project_path]
        for cid in to_remove:
            self._remove_channel_mapping(cid)

    # ── BackgroundRunner ──────────────────────────────────────────────────────

    def _get_runner(self, project_path: str) -> Any:
        with self._runners_lock:
            if project_path not in self._runners:
                from orchid.interfaces.background_runner import BackgroundRunner
                runner = BackgroundRunner(
                    project_path,
                    notification_callback=self._make_notification_cb(project_path),
                )
                self._runners[project_path] = runner
            return self._runners[project_path]

    def _make_notification_cb(self, project_path: str):
        def _cb(event: str, data: dict[str, Any]) -> None:
            from orchid.interfaces.slack_formatter import format_notification
            notify_on = [
                "session_start", "task_start", "task_complete",
                "task_failed", "task_blocked", "session_complete",
            ]
            if event not in notify_on:
                return
            msg = format_notification(event, data)
            if not msg:
                return
            project_name = Path(project_path).name
            tagged = f"[{project_name}] {msg}"
            with self._notify_lock:
                channels = set(self._notify_channels.get(project_path, set()))
            for ch in channels:
                self._post(ch, tagged)
        return _cb

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Build Bolt app, register handlers, start Socket Mode (blocks).

        Commands are registered as Bolt slash commands via app.command() —
        they are invoked with /orchid-<name> in any channel and do NOT
        require @Orchid mentions. Socket Mode means no public URL is needed.
        """
        app = App(token=self.bot_token)
        self._client = WebClient(token=self.bot_token)

        # Create channels for projects discovered before the bot started (T061).
        self._ensure_channels_for_all_projects()

        # Project-context commands (route via channel map).
        # These respond in the channel they're called from; if the channel
        # is not mapped to a project they return a helpful error.
        for cmd, handler in [
            ("/orchid-status", self._handle_status),
            ("/orchid-run", self._handle_run),
            ("/orchid-auto", self._handle_auto),
            ("/orchid-add", self._handle_add),
            ("/orchid-recall", self._handle_recall),
            ("/orchid-search", self._handle_search),
            ("/orchid-inject", self._handle_inject),
            ("/orchid-cancel", self._handle_cancel),
            ("/orchid-approve", self._handle_approve),
            ("/orchid-phase", self._handle_phase),
            ("/orchid-artifacts", self._handle_artifacts),
            ("/orchid-discuss", self._handle_discuss),
        ]:
            app.command(cmd)(handler)

        # Global commands (no project context required)
        app.command("/orchid-projects")(self._handle_projects)
        app.command("/orchid-new")(self._handle_new)
        app.command("/orchid-add-channel")(self._handle_add_channel)
        app.command("/orchid-unlink-channel")(self._handle_unlink_channel)
        app.command("/orchid-help")(self._handle_help)

        app.event("app_mention")(self._handle_mention)

        self._handler = SocketModeHandler(app, self.app_token)
        logger.info("Central Slack bot starting")
        self._handler.start()

    def stop(self) -> None:
        with self._runners_lock:
            for runner in self._runners.values():
                runner.shutdown()
        if self._handler:
            try:
                self._handler.close()
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def send_dm(self, user_id: str, text: str) -> None:
        """Send a DM to a Slack user by their user_id (e.g. 'U012AB3CD').

        Opens a DM conversation via conversations_open, then posts the message.
        Safe to call from cron worker threads. No-op if bot not running.
        """
        if not self._client:
            logger.warning("Slack client not initialised — cannot send DM to %s", user_id)
            return
        try:
            resp = self._client.conversations_open(users=user_id)
            channel_id = resp["channel"]["id"]
            self._post(channel_id, text)
        except Exception as exc:
            logger.warning("Slack send_dm to %s failed: %s", user_id, exc)

    def _post(self, channel: str, text: str, thread_ts: str | None = None) -> str | None:
        if not self._client:
            return None
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        try:
            resp = self._client.chat_postMessage(**kwargs)
            return resp.get("ts")
        except Exception as exc:
            logger.warning("Slack post failed (channel=%s): %s", channel, exc)
            return None

    def _resolve_project(self, command: dict) -> str | None:
        """Look up project_path for the channel this command came from.

        Reloads the channel map from disk on every call so that entries
        written by a previous bot session (or by /orchid-add-channel before
        this session started) are always visible.  The map file is small so
        the I/O cost per command is negligible.
        """
        # Always read the authoritative file so a stale in-memory map
        # (populated when the file was empty at startup) can never cause
        # misrouting.
        self._load_channels()

        channel_id = command.get("channel_id", "")
        with self._map_lock:
            result = self._channel_map.get(channel_id)
            map_snapshot = dict(self._channel_map)
        logger.info(
            "RESOLVE channel_id=%r map=%r result=%r",
            channel_id,
            map_snapshot,
            result,
        )
        return result

    def _get_phase(self, project_path: str) -> str:
        try:
            from orchid.lifecycle import ProjectLifecycle
            lc = ProjectLifecycle.load(Path(project_path))
            return lc.current_phase()
        except Exception:
            return "UNKNOWN"

    def _list_projects(self) -> list[dict[str, Any]]:
        from orchid.lifecycle import ProjectLifecycle
        from orchid.memory.state import TaskStatus
        from orchid.session import Session
        projects = []
        for proj_path in self._discovery.scan():
            # Skip inactive projects
            try:
                import yaml as _yaml
                _oyaml = proj_path / ".orchid.yaml"
                _yd = _yaml.safe_load(_oyaml.read_text(encoding="utf-8")) or {} if _oyaml.exists() else {}
                if not _yd.get("active", True):
                    continue
            except Exception:
                pass
            path_str = str(proj_path)
            name = proj_path.name
            try:
                lc = ProjectLifecycle.load(proj_path)
                phase = lc.current_phase()
                if lc.state.project_name:
                    name = lc.state.project_name
            except Exception:
                phase = "UNKNOWN"
            try:
                s = Session(project_dir=path_str)
                s.load()
                pending = sum(1 for t in s.tasks if t.status == TaskStatus.TODO)
            except Exception:
                pending = 0
            projects.append({"name": name, "path": path_str, "phase": phase, "pending": pending})
        return projects

    # ── Global command handlers ───────────────────────────────────────────────

    def _handle_projects(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        projects = self._list_projects()
        if not projects:
            say(text="No Orchid projects discovered yet.")
            return
        lines = ["*📋 Discovered projects:*\n"]
        for i, p in enumerate(projects, 1):
            pending_str = f" — {p['pending']} pending" if p["pending"] else ""
            lines.append(f"{i}. *{p['name']}* [{p['phase']}]{pending_str}")
        say(text="\n".join(lines))

    def _handle_new(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        from orchid import config as _cfg
        if not _cfg.get("web.allow_user_projects", True):
            respond("❌ Project creation is disabled by admin.")
            return
        text = (command.get("text") or "").strip()
        if not text:
            respond("Usage: /orchid-new <project description>")
            return
        try:
            from orchid.project_creator import ProjectCreator
            slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
            creator = ProjectCreator()
            project_path = creator.create(name=slug, description=text)
            say(text=f"🌱 Created project *{slug}* at `{project_path}`.")
        except Exception as exc:
            say(text=f"❌ Failed to create project: {exc!s:.200}")

    def _handle_add_channel(self, ack, respond, say, command) -> None:
        """Link a Slack channel to a project. Usage: /orchid-add-channel [#channel] --project <name>"""
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        text = (command.get("text") or "").strip()
        channel_id = command.get("channel_id", "")

        # Parse --project flag
        project_name = None
        m = re.search(r"--project\s+(\S+)", text)
        if m:
            project_name = m.group(1)

        # Parse explicit #channel mention
        explicit_channel = None
        mc = re.search(r"<#(\w+)\|[^>]*>", text)
        if mc:
            explicit_channel = mc.group(1)

        target_channel = explicit_channel or channel_id
        if not target_channel:
            respond("Usage: /orchid-add-channel [#channel] --project <name>")
            return

        # Find project path
        target_path = None
        if project_name:
            for proj_path in self._discovery.scan():
                if proj_path.name == project_name or str(proj_path) == project_name:
                    target_path = str(proj_path)
                    break
        if not target_path:
            respond(
                f"❌ Project '{project_name}' not found. Use /orchid-projects to list."
                if project_name
                else "Usage: /orchid-add-channel --project <name>"
            )
            return

        self._add_channel_mapping(target_channel, target_path)
        pname = Path(target_path).name
        say(text=f"✅ Channel linked to project *{pname}*. Commands in this channel will now route to it.")

    def _handle_unlink_channel(self, ack, respond, say, command) -> None:
        """Remove the current channel from the channel map."""
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        channel_id = command.get("channel_id", "")
        path = self._get_project_for_channel(channel_id)
        if not path:
            respond("This channel is not linked to any project.")
            return
        pname = Path(path).name
        self._remove_channel_mapping(channel_id)
        say(text=f"🔌 Channel unlinked from project *{pname}*. Use /orchid-add-channel to relink.")

    def _handle_help(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        say(text=self._help_text())

    def _help_text(self) -> str:
        return (
            "*Orchid Central Bot Commands*\n\n"
            "*Global (any channel):*\n"
            "*/orchid-projects* — list all discovered projects\n"
            "*/orchid-new <description>* — create a new project\n"
            "*/orchid-add-channel --project <name>* — link this channel to a project\n"
            "*/orchid-unlink-channel* — unlink this channel from its project\n"
            "*/orchid-help* — this message\n\n"
            "*Project commands (in a linked channel):*\n"
            "*/orchid-status* — task board\n"
            "*/orchid-run <task_id>* — run a specific task\n"
            "*/orchid-auto* — run all pending tasks\n"
            "*/orchid-add <description>* — add a new task\n"
            "*/orchid-recall <query>* — search vector memory\n"
            "*/orchid-search <query>* — web search\n"
            "*/orchid-inject <text>* — inject context into running agent\n"
            "*/orchid-cancel* — cancel the running task\n"
            "*/orchid-approve* — approve lifecycle gate\n"
            "*/orchid-phase* — show lifecycle phase\n"
            "*/orchid-artifacts* — list generated artifacts\n"
            "*/orchid-discuss <message>* — chat with DiscussionAgent\n\n"
            "You can also @mention Orchid in a linked channel."
        )

    # ── Project command handlers ──────────────────────────────────────────────

    def _handle_status(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        from orchid.interfaces.slack_formatter import format_status, format_status_text
        from orchid.session import Session
        s = Session(project_dir=path)
        s.load()
        phase = self._get_phase(path)
        blocks = format_status(s)
        say(text=f"[{Path(path).name} | {phase}] {format_status_text(s)}", blocks=blocks)

    def _handle_run(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        task_id = (command.get("text") or "").strip().upper()
        if not task_id:
            respond("Usage: /orchid-run <task_id>")
            return
        runner = self._get_runner(path)
        if runner.is_running():
            respond("⚠️ A task is already running.")
            return
        from orchid.session import Session
        s = Session(project_dir=path)
        s.load()
        task = next((t for t in s.tasks if t.id == task_id), None)
        if task is None:
            respond(f"❌ Task {task_id} not found.")
            return
        from orchid.interfaces.slack_formatter import (
            format_task_complete,
            format_task_failed,
            format_task_started,
        )
        channel = command.get("channel_id", "")
        with self._notify_lock:
            self._notify_channels.setdefault(path, set()).add(channel)
        ts = self._post(channel, format_task_started(task_id, task.title))

        def _cb(tid: str, result: str | None, error: str | None) -> None:
            with self._notify_lock:
                self._notify_channels.get(path, set()).discard(channel)
            msg = format_task_failed(tid, error) if error else format_task_complete(tid, result or "")
            self._post(channel, msg, thread_ts=ts)

        runner.run_task(task_id, _cb, self._loop)

    def _handle_auto(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        runner = self._get_runner(path)
        if runner.is_running():
            respond("⚠️ Already running.")
            return
        from orchid.memory.state import TaskStatus
        from orchid.session import Session
        s = Session(project_dir=path)
        s.load()
        pending = [t for t in s.tasks if t.status == TaskStatus.TODO]
        if not pending:
            respond("No pending tasks.")
            return
        from orchid.interfaces.slack_formatter import (
            format_auto_summary,
            format_task_complete,
            format_task_failed,
        )
        channel = command.get("channel_id", "")
        with self._notify_lock:
            self._notify_channels.setdefault(path, set()).add(channel)
        say(text=f"🚀 Starting auto run — {len(pending)} pending tasks…")

        def _on_task(tid: str, result: str | None, error: str | None) -> None:
            msg = format_task_failed(tid, error) if error else format_task_complete(tid, result or "")
            self._post(channel, msg)

        def _on_done(done_ids: list[str], failed_ids: list[str]) -> None:
            with self._notify_lock:
                self._notify_channels.get(path, set()).discard(channel)
            self._post(channel, format_auto_summary(done_ids, failed_ids))

        runner.run_auto(_on_task, _on_done, self._loop)

    def _handle_add(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        text = (command.get("text") or "").strip()
        if not text:
            respond("Usage: /orchid-add <task description>")
            return
        from orchid.memory.state import Task, save_tasks
        from orchid.session import Session
        s = Session(project_dir=path)
        s.load()
        tid = f"T{len(s.tasks) + 1:03d}"
        t = Task(id=tid, title=text, type="draft", priority=2, description=text)
        s.tasks.append(t)
        save_tasks(s.tasks, path)
        say(text=f"✅ Added *{tid}*: {text}  `type=draft p2`")

    def _handle_recall(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        query = (command.get("text") or "").strip()
        if not query:
            respond("Usage: /orchid-recall <query>")
            return
        from orchid import config as cfg
        from orchid.interfaces.slack_formatter import format_recall_results
        from orchid.memory.vector import VectorMemory
        cfg.configure_for_project(path)
        vm = VectorMemory(project_dir=path)
        if not vm.available:
            say(text="⚠️ Vector memory not available.")
            return
        results = vm.query(query, n=3)
        say(text=f"🔍 *Recall:* {query}\n\n{format_recall_results(results)}")

    def _handle_search(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        query = (command.get("text") or "").strip()
        if not query:
            respond("Usage: /orchid-search <query>")
            return
        from orchid import config as cfg
        from orchid.interfaces.slack_formatter import format_search_results
        from orchid.tools.search import WebSearchTool, reset_backend_cache
        cfg.configure_for_project(path)
        reset_backend_cache()
        tool = WebSearchTool(project_name=Path(path).name)
        results = tool.search(query, n=3)
        say(text=f"🌐 *Search:* {query}\n\n{format_search_results(results)}")

    def _handle_inject(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        text = (command.get("text") or "").strip()
        if not text:
            respond("Usage: /orchid-inject <text>")
            return
        runner = self._get_runner(path)
        if not runner.is_running():
            respond("⚠️ No task is currently running.")
            return
        runner.inject(text)
        say(text=f"💉 Injected: {text[:100]}")

    def _handle_cancel(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        runner = self._get_runner(path)
        if runner.cancel():
            say(text="🛑 Cancellation requested.")
        else:
            say(text="Nothing is running.")

    def _handle_approve(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        from orchid.gates import GateStatus, GateSystem
        from orchid.lifecycle import ProjectLifecycle
        lc = ProjectLifecycle.load(Path(path))
        gates = GateSystem(lc)
        next_phases = lc.valid_next_phases()
        if not next_phases:
            say(text="⚠️ No valid transitions from current phase.")
            return
        to_phase = next_phases[0]
        status = gates.check_gate(to_phase)
        if status == GateStatus.BLOCKED:
            say(text=f"🚫 Gate blocked — prerequisites not met for → {to_phase}.")
            return
        gates.approve(to_phase, approver="slack")
        status_after = gates.check_gate(to_phase)
        if status_after == GateStatus.OPEN:
            lc.advance(to_phase)
            say(text=f"✅ Approved! Advanced to *{to_phase}*.")
        else:
            say(text=f"✅ Gate approved for *{to_phase}*. Run agents to advance.")

    def _handle_phase(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        from orchid.lifecycle import ProjectLifecycle
        lc = ProjectLifecycle.load(Path(path))
        phase = lc.current_phase()
        nexts = lc.valid_next_phases()
        artifacts_ok = lc.artifacts_complete()
        say(text=(
            f"📍 *Phase:* {phase}\n"
            f"📦 *Artifacts:* {'✅ complete' if artifacts_ok else '⏳ incomplete'}\n"
            f"➡️ *Can advance to:* {', '.join(nexts) if nexts else 'none'}"
        ))

    def _handle_artifacts(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        proj_path = Path(path)
        artifact_files = [
            "REQUIREMENTS.md", "ARCHITECTURE.md", "MILESTONES.md",
            "tasks.md", "CLAUDE.md", ".orchid/decisions.json",
        ]
        lines = [f"📦 *Artifacts for {proj_path.name}:*\n"]
        for af in artifact_files:
            exists = (proj_path / af).exists()
            lines.append(f"{'✅' if exists else '❌'} {af}")
        say(text="\n".join(lines))

    def _handle_discuss(self, ack, respond, say, command) -> None:
        ack()
        logger.info("CMD %s channel_id=%r", command.get("command", ""), command.get("channel_id", ""))
        path = self._resolve_project(command)
        if not path:
            respond("❌ This channel is not linked to a project. Use /orchid-add-channel.")
            return
        text = (command.get("text") or "").strip()
        if not text:
            respond("Usage: /orchid-discuss <message>")
            return
        try:
            from orchid.agents.discussion_agent import DiscussionAgent
            from orchid.discussion import DiscussionHistory
            agent = DiscussionAgent(project_dir=Path(path))
            history = DiscussionHistory.load(Path(path))
            resp = agent.run(text, history)
            reply_text = resp.message
            if resp.ready_to_advance:
                reply_text += "\n\n✅ *Requirements look complete!* Use /orchid-approve to advance."
            say(text=reply_text)
        except Exception as exc:
            logger.exception("Discussion error: %s", exc)
            say(text=f"⚠️ Discussion error: {exc!s:.200}")

    # ── Mention handler ───────────────────────────────────────────────────────

    def _handle_mention(self, event, say, client) -> None:
        text = event.get("text", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        text = re.sub(r"<@\w+>", "", text).strip()

        path = self._get_project_for_channel(channel)
        if not path:
            say(text=self._help_text(), thread_ts=thread_ts)
            return

        if not text:
            say(text=self._help_text(), thread_ts=thread_ts)
            return

        # Simple intent routing for mentions
        msg_lower = text.lower()
        if re.match(r"(status|tasks|what.*(tasks|pending))", msg_lower):
            from orchid.interfaces.slack_formatter import format_status, format_status_text
            from orchid.session import Session
            s = Session(project_dir=path)
            s.load()
            say(text=format_status_text(s), blocks=format_status(s), thread_ts=thread_ts)
        elif re.match(r"(phase|lifecycle)", msg_lower):
            from orchid.lifecycle import ProjectLifecycle
            lc = ProjectLifecycle.load(Path(path))
            say(text=f"📍 Phase: *{lc.current_phase()}*", thread_ts=thread_ts)
        else:
            say(text=self._help_text(), thread_ts=thread_ts)
