"""Central Telegram bot for Orchid — multi-project, user-scoped routing.

Replaces project-scoped TelegramBot with a single central bot that knows all
discovered projects via ProjectDiscovery and routes per-user based on their
active project context.

Architecture (D0050, D0051):
- User state persisted at ~/.config/orchid/telegram-state.json
- Per-project BackgroundRunners created lazily
- All commands prefixed /orchid_ (underscores — Telegram requires [a-z0-9_])
- Context footer "📌 [project-name | PHASE]" on every reply
- Proactive notifications routed by project subscription

Usage:
    bot = CentralTelegramBot(discovery=discovery, token="...", allowed_users=[123])
    bot.start()   # blocks
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_FILE = Path("~/.config/orchid/telegram-state.json").expanduser()

try:
    from telegram import Update
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
    )
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via temp-file rename."""
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


class CentralTelegramBot:
    """Central Orchid Telegram bot — routes per-user based on active project."""

    def __init__(
        self,
        discovery: Any,
        token: str,
        allowed_users: list[int] | None = None,
        state_file: Path | None = None,
    ) -> None:
        if not _TELEGRAM_AVAILABLE:
            raise ImportError(
                "python-telegram-bot is not installed. "
                "Run: uv pip install 'python-telegram-bot>=20.0'"
            )
        self._discovery = discovery
        self.token = token
        self.allowed_users: set[int] = set(allowed_users or [])
        self._state_file = state_file or _STATE_FILE

        # user_id (str) → {active_project, active_project_path, phase, last_interaction}
        self._user_state: dict[str, dict[str, Any]] = {}
        self._state_lock = threading.Lock()

        # project_path → BackgroundRunner (lazy)
        self._runners: dict[str, Any] = {}
        self._runners_lock = threading.Lock()

        # project_path → set[int] chat_ids subscribed for notifications
        self._subscribers: dict[str, set[int]] = {}
        self._sub_lock = threading.Lock()

        self._app: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._load_state()

    # ── State I/O ─────────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                with self._state_lock:
                    self._user_state = data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load telegram state: %s", exc)

    def _save_state(self) -> None:
        with self._state_lock:
            data = dict(self._user_state)
        try:
            _atomic_write_json(self._state_file, data)
        except Exception as exc:
            logger.warning("Failed to save telegram state: %s", exc)

    # ── Project helpers ───────────────────────────────────────────────────────

    def _get_active_project(self, user_id: int) -> tuple[str | None, str | None]:
        """Return (project_name, project_path) for user, or (None, None)."""
        with self._state_lock:
            entry = self._user_state.get(str(user_id), {})
        return entry.get("active_project"), entry.get("active_project_path")

    def _set_active_project(
        self, user_id: int, name: str, path: str, phase: str = "NEW"
    ) -> None:
        with self._state_lock:
            self._user_state[str(user_id)] = {
                "active_project": name,
                "active_project_path": path,
                "phase": phase,
                "last_interaction": datetime.now(UTC).isoformat(),
            }
        self._save_state()

    def _list_projects(self) -> list[dict[str, Any]]:
        """Scan discovery for active projects with name, path, phase, pending count."""
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

    def _get_phase(self, project_path: str) -> str:
        try:
            from orchid.lifecycle import ProjectLifecycle
            lc = ProjectLifecycle.load(Path(project_path))
            return lc.current_phase()
        except Exception:
            return "UNKNOWN"

    def _context_footer(self, user_id: int) -> str:
        name, path = self._get_active_project(user_id)
        if not name or not path:
            return "\n\n📌 [no active project — use /orchid_projects]"
        phase = self._get_phase(path)
        return f"\n\n📌 [{name} | {phase}]"

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
        """Return an async notification callback for a specific project."""
        async def _cb(event: str, data: dict[str, Any]) -> None:
            from orchid.interfaces.telegram_formatter import format_notification
            msg = format_notification(event, data)
            if msg is None:
                return
            # Tag with project name
            project_name = Path(project_path).name
            tagged = f"🌸 [{project_name}] {msg}"
            with self._sub_lock:
                chat_ids = set(self._subscribers.get(project_path, set()))
            for chat_id in chat_ids:
                try:
                    await self._app.bot.send_message(chat_id=chat_id, text=tagged)
                except Exception as exc:
                    logger.warning("Notification send failed (chat=%s): %s", chat_id, exc)
        return _cb

    def _subscribe(self, project_path: str, chat_id: int) -> None:
        with self._sub_lock:
            self._subscribers.setdefault(project_path, set()).add(chat_id)

    def _unsubscribe(self, project_path: str, chat_id: int) -> None:
        with self._sub_lock:
            self._subscribers.get(project_path, set()).discard(chat_id)

    def send_dm(self, chat_id: int, text: str) -> None:
        """Send a DM to a specific Telegram chat_id from a sync thread.

        Uses run_coroutine_threadsafe to schedule on the bot's event loop.
        Safe to call from cron worker threads. No-op if bot not running.
        """
        if self._app is None:
            logger.warning("Telegram bot not initialised — cannot send DM to %s", chat_id)
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("Telegram event loop not running — cannot send DM to %s", chat_id)
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._app.bot.send_message(chat_id=chat_id, text=text),
                loop,
            )
        except Exception as exc:
            logger.warning("Telegram send_dm to %s failed: %s", chat_id, exc)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Build Application and start polling (blocks until stopped).

        Uses a manually managed asyncio event loop with no signal handlers,
        so it is safe to call from a background thread (D0050).
        run_polling() is intentionally avoided because it calls
        signal.set_wakeup_fd() which only works on the main thread.
        """
        if not self.allowed_users:
            logger.warning(
                "TELEGRAM_ALLOWED_USERS not set — bot accepts messages from ALL users."
            )
        self._app = Application.builder().token(self.token).build()

        handlers = [
            ("orchid_projects", self._cmd_projects),
            ("orchid_switch", self._cmd_switch),
            ("orchid_status", self._cmd_status),
            ("orchid_run", self._cmd_run),
            ("orchid_auto", self._cmd_auto),
            ("orchid_add", self._cmd_add),
            ("orchid_recall", self._cmd_recall),
            ("orchid_search", self._cmd_search),
            ("orchid_inject", self._cmd_inject),
            ("orchid_approve", self._cmd_approve),
            ("orchid_phase", self._cmd_phase),
            ("orchid_artifacts", self._cmd_artifacts),
            ("orchid_new", self._cmd_new),
            ("orchid_discuss", self._cmd_discuss),
            ("orchid_cancel", self._cmd_cancel),
            ("orchid_help", self._cmd_help),
            # Keep start/help for discoverability
            ("start", self._cmd_help),
            ("help", self._cmd_help),
        ]
        for name, handler in handlers:
            self._app.add_handler(CommandHandler(name, self._guard(handler)))

        logger.info("Central Telegram bot starting")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_async())
        finally:
            self._loop.close()
            self._loop = None

    async def _run_async(self) -> None:
        """Async polling loop — no signal handlers, safe in background thread."""
        self._stop_event = asyncio.Event()
        await self._app.initialize()
        await self._app.updater.start_polling(drop_pending_updates=True)
        await self._app.start()
        logger.info("Central Telegram bot polling started")
        await self._stop_event.wait()
        logger.info("Central Telegram bot stop requested — shutting down")
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    def stop(self) -> None:
        with self._runners_lock:
            for runner in self._runners.values():
                runner.shutdown()
        # Signal the async loop to exit cleanly
        if self._loop and self._stop_event and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def on_project_added(self, project_path: str) -> None:
        """Called when a new project is discovered."""
        logger.info("CentralTelegramBot: project added %s", project_path)
        # Broadcast to all subscribed chat IDs (across all projects)
        all_chats: set[int] = set()
        with self._sub_lock:
            for chat_ids in self._subscribers.values():
                all_chats |= chat_ids
        if self._app and all_chats:
            name = Path(project_path).name
            msg = f"🌸 New project discovered: {name}"
            loop = self._get_loop()
            for chat_id in all_chats:
                asyncio.run_coroutine_threadsafe(
                    self._app.bot.send_message(chat_id=chat_id, text=msg),
                    loop,
                )

    def on_project_removed(self, project_path: str) -> None:
        logger.info("CentralTelegramBot: project removed %s", project_path)
        with self._runners_lock:
            runner = self._runners.pop(project_path, None)
        if runner:
            runner.shutdown()

    # ── Auth guard ────────────────────────────────────────────────────────────

    def _guard(self, handler):
        async def _wrapped(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            user_id = update.effective_user.id if update.effective_user else None
            if self.allowed_users and user_id not in self.allowed_users:
                logger.warning("Rejected message from user_id=%s", user_id)
                return
            try:
                await handler(update, ctx)
            except Exception as exc:
                logger.exception("Handler error: %s", exc)
                await self._reply(update, f"⚠️ Error: {exc!s:.200}")
        return _wrapped

    # ── Reply helpers ─────────────────────────────────────────────────────────

    async def _reply(
        self,
        update: Update,
        text: str,
        parse_mode: str | None = None,
        with_footer: bool = True,
    ) -> None:
        """Reply to a message. Default parse_mode=None (plain text) so that
        dynamic content — project names, task titles, paths, agent output —
        never triggers Telegram markdown parse errors."""
        user_id = update.effective_user.id if update.effective_user else 0
        if with_footer:
            text = text + self._context_footer(user_id)
        if update.message:
            await update.message.reply_text(
                text, parse_mode=parse_mode, disable_web_page_preview=True
            )

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Return the bot's running event loop (set during start())."""
        if self._loop and not self._loop.is_closed():
            return self._loop
        return asyncio.get_event_loop()

    def _require_project(self, user_id: int) -> tuple[str | None, str | None]:
        """Return (name, path) or (None, None) — caller must check."""
        return self._get_active_project(user_id)

    # ── Command handlers ──────────────────────────────────────────────────────

    async def _cmd_projects(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        projects = self._list_projects()
        if not projects:
            await self._reply(update, "No Orchid projects discovered yet.", with_footer=False)
            return
        lines = ["📋 Discovered projects:\n"]
        for i, p in enumerate(projects, 1):
            pending_str = f" — {p['pending']} pending" if p["pending"] else ""
            lines.append(f"{i}. {p['name']} [{p['phase']}]{pending_str}")
        lines.append("\nUse /orchid_switch <name or number> to switch.")
        await self._reply(update, "\n".join(lines), with_footer=False)

    async def _cmd_switch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /orchid_switch <name or number>", with_footer=False)
            return
        query = " ".join(args).strip()
        projects = self._list_projects()
        matched = None

        # Try numeric index
        if query.isdigit():
            idx = int(query) - 1
            if 0 <= idx < len(projects):
                matched = projects[idx]
        if matched is None:
            # Try name match (case-insensitive, partial)
            q = query.lower()
            for p in projects:
                if q == p["name"].lower() or q in p["name"].lower():
                    matched = p
                    break

        if matched is None:
            names = ", ".join(p["name"] for p in projects)
            await self._reply(
                update,
                f"❌ No project matching '{query}'. Available: {names}",
                with_footer=False,
            )
            return

        user_id = update.effective_user.id
        self._set_active_project(
            user_id, matched["name"], matched["path"], matched["phase"]
        )
        await self._reply(update, f"✅ Now working on: {matched['name']}", with_footer=True)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project. Use /orchid_projects to list.", with_footer=False)
            return
        from orchid.interfaces.telegram_formatter import format_status
        from orchid.session import Session
        s = Session(project_dir=path)
        s.load()
        await self._reply(update, format_status(s))

    async def _cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project. Use /orchid_switch first.", with_footer=False)
            return
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /orchid_run <task_id>")
            return
        task_id = args[0].upper()
        runner = self._get_runner(path)
        if runner.is_running():
            await self._reply(update, "⚠️ A task is already running. Use /orchid_cancel first.")
            return
        from orchid.session import Session
        s = Session(project_dir=path)
        s.load()
        task = next((t for t in s.tasks if t.id == task_id), None)
        if task is None:
            await self._reply(update, f"❌ Task {task_id} not found.")
            return
        from orchid.interfaces.telegram_formatter import (
            format_task_complete,
            format_task_failed,
            format_task_started,
        )
        chat_id = update.effective_chat.id
        self._subscribe(path, chat_id)
        await self._reply(update, format_task_started(task_id, task.title))
        loop = self._get_loop()

        async def _on_done(tid: str, result: str | None, error: str | None) -> None:
            self._unsubscribe(path, chat_id)
            msg = format_task_failed(tid, error) if error else format_task_complete(tid, result or "")
            await self._app.bot.send_message(chat_id=chat_id, text=msg)

        runner.run_task(task_id, _on_done, loop)

    async def _cmd_auto(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project. Use /orchid_switch first.", with_footer=False)
            return
        runner = self._get_runner(path)
        if runner.is_running():
            await self._reply(update, "⚠️ Already running. Use /orchid_cancel first.")
            return
        from orchid.memory.state import TaskStatus
        from orchid.session import Session
        s = Session(project_dir=path)
        s.load()
        pending = [t for t in s.tasks if t.status == TaskStatus.TODO]
        if not pending:
            await self._reply(update, "No pending tasks.")
            return
        from orchid.interfaces.telegram_formatter import (
            format_auto_summary,
            format_task_complete,
            format_task_failed,
        )
        chat_id = update.effective_chat.id
        self._subscribe(path, chat_id)
        await self._reply(update, f"🚀 Starting auto run — {len(pending)} pending tasks…")
        loop = self._get_loop()

        async def _on_task(tid: str, result: str | None, error: str | None) -> None:
            msg = format_task_failed(tid, error) if error else format_task_complete(tid, result or "")
            await self._app.bot.send_message(chat_id=chat_id, text=msg)

        async def _on_done(done_ids: list[str], failed_ids: list[str]) -> None:
            self._unsubscribe(path, chat_id)
            await self._app.bot.send_message(chat_id=chat_id, text=format_auto_summary(done_ids, failed_ids))

        runner.run_auto(_on_task, _on_done, loop)

    async def _cmd_add(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project.", with_footer=False)
            return
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /orchid_add <task description>")
            return
        description = " ".join(args)
        from orchid.memory.state import Task, save_tasks
        from orchid.session import Session
        s = Session(project_dir=path)
        s.load()
        tid = f"T{len(s.tasks) + 1:03d}"
        t = Task(id=tid, title=description, type="draft", priority=2, description=description)
        s.tasks.append(t)
        save_tasks(s.tasks, path)
        await self._reply(update, f"✅ Added {tid}: {description}  (type=draft, p2)")

    async def _cmd_inject(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project.", with_footer=False)
            return
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /orchid_inject <text>")
            return
        runner = self._get_runner(path)
        if not runner.is_running():
            await self._reply(update, "⚠️ No task is currently running.")
            return
        text = " ".join(args)
        runner.inject(text)
        await self._reply(update, f"💉 Injected: {text[:100]}")

    async def _cmd_recall(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project.", with_footer=False)
            return
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /orchid_recall <query>")
            return
        from orchid import config as cfg
        from orchid.interfaces.telegram_formatter import format_recall_results
        from orchid.memory.vector import VectorMemory
        query = " ".join(args)
        cfg.configure_for_project(path)
        vm = VectorMemory(project_dir=path)
        if not vm.available:
            await self._reply(update, "⚠️ Vector memory not available.")
            return
        results = vm.query(query, n=3)
        await self._reply(update, f"🔍 Recall: {query}\n\n" + format_recall_results(results))

    async def _cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project.", with_footer=False)
            return
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /orchid_search <query>")
            return
        from orchid import config as cfg
        from orchid.interfaces.telegram_formatter import format_search_results
        from orchid.tools.search import WebSearchTool, reset_backend_cache
        query = " ".join(args)
        await self._reply(update, f"🔎 Searching: {query}…")
        cfg.configure_for_project(path)
        reset_backend_cache()
        tool = WebSearchTool(project_name=Path(path).name)
        results = tool.search(query, n=3)
        await self._reply(update, f"🌐 Search: {query}\n\n" + format_search_results(results))

    async def _cmd_approve(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project.", with_footer=False)
            return
        from orchid.gates import GateStatus, GateSystem
        from orchid.lifecycle import ProjectLifecycle
        lc = ProjectLifecycle.load(Path(path))
        gates = GateSystem(lc)
        next_phases = lc.valid_next_phases()
        if not next_phases:
            await self._reply(update, "⚠️ No valid transitions from current phase.")
            return
        # Approve the first available forward transition
        to_phase = next_phases[0]
        status = gates.check_gate(to_phase)
        if status == GateStatus.BLOCKED:
            await self._reply(update, f"🚫 Gate blocked — prerequisites not met for → {to_phase}.")
            return
        gates.approve(to_phase, approver="telegram")
        status_after = gates.check_gate(to_phase)
        if status_after == GateStatus.OPEN:
            lc.advance(to_phase)
            await self._reply(update, f"✅ Approved! Advanced: {lc.state.phase} → {to_phase}")
        else:
            await self._reply(update, f"✅ Gate approved for {to_phase}. Run agents to advance.")

    async def _cmd_phase(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project.", with_footer=False)
            return
        from orchid.lifecycle import ProjectLifecycle
        lc = ProjectLifecycle.load(Path(path))
        phase = lc.current_phase()
        nexts = lc.valid_next_phases()
        artifacts_ok = lc.artifacts_complete()
        lines = [
            f"📍 Phase: {phase}",
            f"📦 Artifacts: {'✅ complete' if artifacts_ok else '⏳ incomplete'}",
            f"➡️ Can advance to: {', '.join(nexts) if nexts else 'none'}",
        ]
        await self._reply(update, "\n".join(lines))

    async def _cmd_artifacts(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project.", with_footer=False)
            return
        proj_path = Path(path)
        artifact_files = [
            "REQUIREMENTS.md", "ARCHITECTURE.md", "MILESTONES.md",
            "tasks.md", "CLAUDE.md", ".orchid/decisions.json",
        ]
        lines = [f"📦 Artifacts for {name}:\n"]
        for af in artifact_files:
            exists = (proj_path / af).exists()
            lines.append(f"{'✅' if exists else '❌'} {af}")
        await self._reply(update, "\n".join(lines))

    async def _cmd_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /orchid_new <project description>", with_footer=False)
            return
        description = " ".join(args)
        try:
            import re

            from orchid.project_creator import ProjectCreator
            slug = re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")[:40]
            creator = ProjectCreator()
            project_path = creator.create(name=slug, description=description)
            user_id = update.effective_user.id
            self._set_active_project(user_id, slug, str(project_path), "DISCUSSING")
            await self._reply(
                update,
                f"🌱 Created project '{slug}' at {project_path}.\n"
                f"Use /orchid_discuss to start the requirements conversation.",
            )
        except Exception as exc:
            await self._reply(update, f"❌ Failed to create project: {exc!s:.200}")

    async def _cmd_discuss(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project. Use /orchid_switch first.", with_footer=False)
            return
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /orchid_discuss <message>")
            return
        message = " ".join(args)
        try:
            from orchid.agents.discussion_agent import DiscussionAgent
            from orchid.discussion import DiscussionHistory
            agent = DiscussionAgent(project_dir=Path(path))
            history = DiscussionHistory.load(Path(path))
            resp = agent.run(message, history)
            reply_text = resp.message
            if resp.ready_to_advance:
                reply_text += "\n\n✅ Requirements look complete! Use /orchid_approve to advance."
            await self._reply(update, reply_text)
        except Exception as exc:
            logger.exception("Discussion error: %s", exc)
            await self._reply(update, f"⚠️ Discussion error: {exc!s:.200}")

    async def _cmd_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        name, path = self._require_project(user_id)
        if not path:
            await self._reply(update, "❌ No active project.", with_footer=False)
            return
        runner = self._get_runner(path)
        if runner.cancel():
            await self._reply(update, "🛑 Cancellation requested.")
        else:
            await self._reply(update, "Nothing is running.")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "🌸 *Orchid Central Bot*\n\n"
            "*Project navigation:*\n"
            "/orchid\\_projects — list all discovered projects\n"
            "/orchid\\_switch <name|#> — switch active project\n"
            "/orchid\\_status — task board\n"
            "/orchid\\_phase — lifecycle phase info\n"
            "/orchid\\_artifacts — list generated artifacts\n\n"
            "*Running tasks:*\n"
            "/orchid\\_run <task\\_id> — run a specific task\n"
            "/orchid\\_auto — run all pending tasks\n"
            "/orchid\\_cancel — cancel running task\n"
            "/orchid\\_add <description> — add a new task\n"
            "/orchid\\_inject <text> — inject context into running agent\n\n"
            "*Research:*\n"
            "/orchid\\_recall <query> — search vector memory\n"
            "/orchid\\_search <query> — web search\n\n"
            "*Lifecycle:*\n"
            "/orchid\\_new <description> — create a new project\n"
            "/orchid\\_discuss <message> — chat with DiscussionAgent\n"
            "/orchid\\_approve — approve lifecycle gate\n"
        )
        if update.message:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
