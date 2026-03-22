"""Telegram bot interface for Orchid.

Thin layer — delegates all business logic to orchestrator, agents, and memory.
No business logic lives here.

Usage:
    from orchid.interfaces.telegram_bot import TelegramBot
    bot = TelegramBot(project_path="/path/to/project", token="...", allowed_users=[123])
    bot.start()   # blocks until SIGINT/SIGTERM
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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


class TelegramBot:
    """Orchid Telegram bot — thin interface over the orchestrator."""

    def __init__(
        self,
        project_path: str,
        token: str,
        allowed_users: list[int] | None = None,
        multi_project: bool = False,
        extra_projects: list[str] | None = None,
    ) -> None:
        if not _TELEGRAM_AVAILABLE:
            raise ImportError(
                "python-telegram-bot is not installed. "
                "Run: uv pip install 'python-telegram-bot>=20.0'"
            )
        self.project_path = str(Path(project_path).resolve())
        self.token = token
        self.allowed_users: set[int] = set(allowed_users or [])
        self.multi_project = multi_project
        # All projects in multi mode (including primary)
        self._all_projects: list[str] = (
            [self.project_path] + [str(Path(p).resolve()) for p in (extra_projects or [])]
            if multi_project
            else [self.project_path]
        )

        from orchid.interfaces.background_runner import BackgroundRunner
        self._runner = BackgroundRunner(
            self.project_path,
            notification_callback=self._on_notification,
        )
        self._app: Any = None
        # Chat IDs that initiated /auto or /run — for proactive notifications
        self._notify_chat_ids: set[int] = set()
        # Multi-project runner (lazy-created in /auto when multi_project=True)
        self._multi_thread: Any = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Build the Application and start polling (blocks until stopped)."""
        if not self.allowed_users:
            logger.warning(
                "TELEGRAM_ALLOWED_USERS not set — bot accepts messages from ALL users. "
                "Set this in .env for production use."
            )

        self._app = (
            Application.builder()
            .token(self.token)
            .build()
        )

        handlers = [
            ("start", self._cmd_start),
            ("help", self._cmd_help),
            ("status", self._cmd_status),
            ("run", self._cmd_run),
            ("auto", self._cmd_auto),
            ("add", self._cmd_add),
            ("recall", self._cmd_recall),
            ("search", self._cmd_search),
            ("decide", self._cmd_decide),
            ("cancel", self._cmd_cancel),
            ("inject", self._cmd_inject),
        ]
        for name, handler in handlers:
            self._app.add_handler(CommandHandler(name, self._guard(handler)))

        logger.info("Telegram bot starting (project=%s)", self.project_path)
        self._app.run_polling(drop_pending_updates=True)

    def stop(self) -> None:
        """Graceful shutdown."""
        self._runner.shutdown()
        if self._app:
            self._app.stop()

    # ── Auth guard ─────────────────────────────────────────────────────────────

    def _guard(self, handler):
        """Wrap a handler with user-whitelist check."""
        async def _wrapped(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            user_id = update.effective_user.id if update.effective_user else None
            if self.allowed_users and user_id not in self.allowed_users:
                logger.warning("Rejected message from user_id=%s", user_id)
                return
            try:
                await handler(update, ctx)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Handler error: %s", exc)
                await self._reply(update, f"⚠️ Error: {exc!s:.200}")
        return _wrapped

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    async def _reply(update: Update, text: str, parse_mode: str | None = None) -> None:
        if update.message:
            await update.message.reply_text(text, parse_mode=parse_mode)

    def _make_session(self):
        from orchid.session import Session
        s = Session(project_dir=self.project_path)
        s.load()
        return s

    async def _on_notification(self, event: str, data: dict[str, Any]) -> None:
        """Handle lifecycle notifications from BackgroundRunner (single-project)."""
        from orchid.interfaces.telegram_formatter import format_notification
        msg = format_notification(event, data)
        if msg is None:
            return
        await self._broadcast(msg)

    async def _on_multi_notification(self, notification: dict[str, Any]) -> None:
        """Handle notifications from MultiOrchid coordinator."""
        from orchid.interfaces.multi_formatter import format_notification as fmt_multi
        event = notification.get("event", "")
        project = notification.get("project", "")
        data = notification.get("data", {})
        msg = fmt_multi(event, project, data)
        if msg is None:
            return
        await self._broadcast(msg)

    async def _broadcast(self, text: str) -> None:
        """Send text to all subscribed chat IDs."""
        for chat_id in list(self._notify_chat_ids):
            try:
                await self._app.bot.send_message(chat_id=chat_id, text=text)
            except Exception as exc:
                logger.warning("Notification send failed (chat_id=%s): %s", chat_id, exc)

    # ── Command handlers ───────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        session = self._make_session()
        text = (
            f"👋 *Orchid* — {session.project_name}\n\n"
            "Available commands:\n"
            "/status — task board\n"
            "/run <task\\_id> — run a task\n"
            "/auto — run all pending tasks\n"
            "/add <description> — add a task\n"
            "/inject <text> — inject context into running agent\n"
            "/recall <query> — search memory\n"
            "/search <query> — web search\n"
            "/decide <title> | <decision> | <rationale> — record decision\n"
            "/cancel — cancel running task\n"
            "/help — this message"
        )
        await self._reply(update, text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._cmd_start(update, ctx)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if self.multi_project and len(self._all_projects) > 1:
            await self._cmd_status_multi(update)
            return
        from orchid.interfaces.telegram_formatter import format_status
        session = self._make_session()
        await self._reply(update, format_status(session))

    async def _cmd_status_multi(self, update: Update) -> None:
        """Show aggregate status across all configured projects."""
        from orchid.session import Session

        lines = ["📊 Multi-project status", ""]
        for proj_path in self._all_projects:
            try:
                s = Session(project_dir=proj_path)
                s.load()
                from orchid.memory.state import TaskStatus
                todo = sum(1 for t in s.tasks if t.status == TaskStatus.TODO)
                done = sum(1 for t in s.tasks if t.status == TaskStatus.DONE)
                inprog = sum(1 for t in s.tasks if t.status == TaskStatus.IN_PROGRESS)
                blocked = sum(1 for t in s.tasks if t.status == TaskStatus.BLOCKED)
                name = s.project_name
                parts = []
                if inprog:
                    parts.append(f"{inprog} running")
                if todo:
                    parts.append(f"{todo} pending")
                if done:
                    parts.append(f"{done} done")
                if blocked:
                    parts.append(f"{blocked} blocked")
                lines.append(f"• {name}: {', '.join(parts) if parts else 'no tasks'}")
            except Exception as exc:
                lines.append(f"• {Path(proj_path).name}: error — {exc}")
        await self._reply(update, "\n".join(lines))

    async def _cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from orchid.interfaces.telegram_formatter import (
            format_task_complete,
            format_task_failed,
            format_task_started,
        )
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /run <task\\_id>  e.g. /run T001")
            return

        task_id = args[0].upper()
        loop = self._get_loop()

        if self._runner.is_running():
            await self._reply(update, "⚠️ A task is already running. Use /cancel first.")
            return

        # Verify task exists
        session = self._make_session()
        task = next((t for t in session.tasks if t.id == task_id), None)
        if task is None:
            await self._reply(update, f"❌ Task {task_id} not found.")
            return

        await self._reply(update, format_task_started(task_id, task.title))

        chat_id = update.effective_chat.id
        self._notify_chat_ids.add(chat_id)

        async def on_done(tid: str, result: str | None, error: str | None) -> None:
            self._notify_chat_ids.discard(chat_id)
            if error:
                msg = format_task_failed(tid, error)
            else:
                msg = format_task_complete(tid, result or "")
            await self._app.bot.send_message(chat_id=chat_id, text=msg)

        self._runner.run_task(task_id, on_done, loop)

    async def _cmd_auto(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if self.multi_project and len(self._all_projects) > 1:
            await self._cmd_auto_multi(update)
            return

        from orchid.interfaces.telegram_formatter import (
            format_auto_summary,
            format_task_complete,
            format_task_failed,
        )
        if self._runner.is_running():
            await self._reply(update, "⚠️ Already running. Use /cancel first.")
            return

        session = self._make_session()
        from orchid.memory.state import TaskStatus
        pending = [t for t in session.tasks if t.status == TaskStatus.TODO]
        if not pending:
            await self._reply(update, "No pending tasks.")
            return

        await self._reply(update, f"🚀 Starting auto run — {len(pending)} pending tasks…")
        chat_id = update.effective_chat.id
        loop = self._get_loop()
        self._notify_chat_ids.add(chat_id)

        async def on_task(tid: str, result: str | None, error: str | None) -> None:
            if error:
                msg = format_task_failed(tid, error)
            else:
                msg = format_task_complete(tid, result or "")
            await self._app.bot.send_message(chat_id=chat_id, text=msg)

        async def on_done(done_ids: list[str], failed_ids: list[str]) -> None:
            self._notify_chat_ids.discard(chat_id)
            msg = format_auto_summary(done_ids, failed_ids)
            await self._app.bot.send_message(chat_id=chat_id, text=msg)

        self._runner.run_auto(on_task, on_done, loop)

    async def _cmd_auto_multi(self, update: Update) -> None:
        """Start a multi-project run — all projects in parallel worker processes."""
        import asyncio
        import threading

        from orchid.multi import MultiOrchid

        if self._multi_thread and self._multi_thread.is_alive():
            await self._reply(update, "⚠️ Multi-project run already in progress.")
            return

        await self._reply(
            update,
            f"🚀 Starting multi-project run — {len(self._all_projects)} project(s)…"
        )
        chat_id = update.effective_chat.id
        self._notify_chat_ids.add(chat_id)
        loop = self._get_loop()

        def _notif_cb(notification: dict) -> None:
            asyncio.run_coroutine_threadsafe(
                self._on_multi_notification(notification), loop
            )

        def _run() -> None:
            orch = MultiOrchid(
                projects=self._all_projects,
                notification_callback=_notif_cb,
            )
            try:
                orch.start()
            except Exception as exc:
                logger.exception("Multi-project run error: %s", exc)
            finally:
                self._notify_chat_ids.discard(chat_id)

        self._multi_thread = threading.Thread(target=_run, daemon=True, name="orchid-multi-bot")
        self._multi_thread.start()

    async def _cmd_add(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /add <task description>")
            return

        description = " ".join(args)
        session = self._make_session()
        from orchid.memory.state import Task, save_tasks

        tid = f"T{len(session.tasks) + 1:03d}"
        t = Task(id=tid, title=description, type="draft", priority=2, description=description)
        session.tasks.append(t)
        save_tasks(session.tasks, self.project_path)
        await self._reply(update, f"✅ Added {tid}: {description}  (type=draft, p2)")

    async def _cmd_inject(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Inject context into the running agent."""
        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /inject <text>")
            return

        text = " ".join(args)
        if not self._runner.is_running():
            await self._reply(update, "⚠️ No task is currently running.")
            return

        self._runner.inject(text)
        await self._reply(update, f"💉 Injected: {text[:100]}")

    async def _cmd_recall(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from orchid import config as cfg
        from orchid.interfaces.telegram_formatter import format_recall_results
        from orchid.memory.vector import VectorMemory

        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /recall <query>")
            return

        query = " ".join(args)
        cfg.configure_for_project(self.project_path)
        vm = VectorMemory(project_dir=self.project_path)
        if not vm.available:
            await self._reply(update, "⚠️ Vector memory not available for this project.")
            return

        n = cfg.get("vector_memory.n_results", 5)
        results = vm.query(query, n=min(n, 3))
        await self._reply(update, f"🔍 Recall: {query}\n\n" + format_recall_results(results))

    async def _cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from orchid import config as cfg
        from orchid.interfaces.telegram_formatter import format_search_results
        from orchid.tools.search import WebSearchTool, reset_backend_cache

        args = ctx.args or []
        if not args:
            await self._reply(update, "Usage: /search <query>")
            return

        query = " ".join(args)
        await self._reply(update, f"🔎 Searching: {query}…")

        cfg.configure_for_project(self.project_path)
        reset_backend_cache()

        vector_memory = None
        if cfg.get("web_search.embed_results", True) and cfg.get("vector_memory.enabled", True):
            from orchid.memory.vector import VectorMemory
            vector_memory = VectorMemory(project_dir=self.project_path)

        tool = WebSearchTool(vector_memory=vector_memory, project_name=Path(self.project_path).name)
        results = tool.search(query, n=3)
        await self._reply(update, f"🌐 Search: {query}\n\n" + format_search_results(results))

    async def _cmd_decide(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.message.text if update.message else ""
        # Strip /decide prefix
        body = text.partition(" ")[2].strip()
        if not body or "|" not in body:
            await self._reply(update, "Usage: /decide <title> | <decision> | <rationale>")
            return

        parts = [p.strip() for p in body.split("|", 2)]
        title = parts[0]
        decision = parts[1] if len(parts) > 1 else ""
        rationale = parts[2] if len(parts) > 2 else ""

        from orchid.memory.decisions import record_decision
        rec = record_decision(title, decision, rationale, project_dir=self.project_path)
        await self._reply(update, f"📝 Recorded {rec['id']}: {title}")

    async def _cmd_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if self._runner.cancel():
            await self._reply(update, "🛑 Cancellation requested. Task will stop at next checkpoint.")
        else:
            await self._reply(update, "Nothing is running.")

    # ── Utilities ──────────────────────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        import asyncio
        return asyncio.get_event_loop()
