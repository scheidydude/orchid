"""Slack bot interface for Orchid.

Thin layer — delegates all business logic to orchestrator, agents, and memory.
No business logic lives here. Follows the same pattern as telegram_bot.py.

Architecture (D0024):
- Socket Mode (slack-bolt + SocketModeHandler) — no public URL required
- BackgroundRunner for non-blocking agent execution
- Thread-per-task for progress updates (D0025)
- Shared BackgroundRunner pattern same as Telegram (D0026)

Usage:
    from orchid.interfaces.slack_bot import SlackBot
    bot = SlackBot(
        project_path="/path/to/project",
        bot_token="xoxb-...",
        app_token="xapp-...",
    )
    bot.start()   # blocks until SIGINT/SIGTERM
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from slack_sdk import WebClient
    _SLACK_AVAILABLE = True
except ImportError:
    _SLACK_AVAILABLE = False


class SlackBot:
    """Orchid Slack bot — thin interface over the orchestrator."""

    def __init__(
        self,
        project_path: str,
        bot_token: str,
        app_token: str,
        default_channel: str = "",
        multi_project: bool = False,
        extra_projects: list[str] | None = None,
    ) -> None:
        if not _SLACK_AVAILABLE:
            raise ImportError(
                "slack-bolt is not installed. "
                "Run: uv pip install 'slack-bolt>=1.18.0'"
            )
        self.project_path = str(Path(project_path).resolve())
        self.bot_token = bot_token
        self.app_token = app_token
        self.default_channel = default_channel
        self.multi_project = multi_project
        self._all_projects: list[str] = (
            [self.project_path] + [str(Path(p).resolve()) for p in (extra_projects or [])]
            if multi_project
            else [self.project_path]
        )

        # Dedicated asyncio loop for BackgroundRunner callbacks
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="orchid-slack-loop",
        )
        self._loop_thread.start()

        from orchid.interfaces.background_runner import BackgroundRunner
        self._runner = BackgroundRunner(
            self.project_path,
            notification_callback=self._on_notification,
        )

        # Slack Web API client (set in start())
        self._client: Any = None
        # Thread tracking: {task_id: {"channel": str, "thread_ts": str}}
        self._task_threads: dict[str, dict[str, str]] = {}
        self._notify_channels: set[str] = set()

        # Multi-project runner thread
        self._multi_thread: threading.Thread | None = None

        self._handler: Any = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Build the Bolt app, register handlers, and start Socket Mode (blocks)."""
        app = App(token=self.bot_token)
        self._client = WebClient(token=self.bot_token)

        # Register slash commands
        app.command("/orchid-status")(self._handle_status)
        app.command("/orchid-run")(self._handle_run)
        app.command("/orchid-auto")(self._handle_auto)
        app.command("/orchid-add")(self._handle_add)
        app.command("/orchid-recall")(self._handle_recall)
        app.command("/orchid-search")(self._handle_search)
        app.command("/orchid-inject")(self._handle_inject)
        app.command("/orchid-help")(self._handle_help)

        # App mention handler
        app.event("app_mention")(self._handle_mention)

        self._handler = SocketModeHandler(app, self.app_token)
        logger.info("Slack bot starting (project=%s)", self.project_path)
        self._handler.start()

    def stop(self) -> None:
        """Graceful shutdown."""
        self._runner.shutdown()
        if self._handler:
            try:
                self._handler.close()
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _make_session(self):
        from orchid.session import Session
        s = Session(project_dir=self.project_path)
        s.load()
        return s

    def _post(self, channel: str, text: str, thread_ts: str | None = None, blocks: list | None = None) -> str | None:
        """Post a message; return the message ts or None on error."""
        if not self._client:
            return None
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        if blocks:
            kwargs["blocks"] = blocks
        try:
            resp = self._client.chat_postMessage(**kwargs)
            return resp.get("ts")
        except Exception as exc:
            logger.warning("Slack post failed (channel=%s): %s", channel, exc)
            return None

    def _broadcast(self, text: str, blocks: list | None = None) -> None:
        """Send to all subscribed channels."""
        for channel in list(self._notify_channels):
            self._post(channel, text, blocks=blocks)

    def _on_notification(self, event: str, data: dict[str, Any]) -> None:
        """Handle lifecycle notifications from BackgroundRunner (sync callback)."""
        from orchid.interfaces.slack_formatter import format_notification
        notify_on = ["session_start", "task_start", "task_complete",
                     "task_failed", "task_blocked", "session_complete"]

        if event not in notify_on:
            return

        msg = format_notification(event, data)
        if msg is None:
            return

        task_id = data.get("task_id")

        # Task progress → reply in task thread if we have one
        if task_id and task_id in self._task_threads:
            info = self._task_threads[task_id]
            self._post(info["channel"], msg, thread_ts=info["thread_ts"])

            # On completion, also clear the thread mapping and broadcast summary
            if event in ("task_complete", "task_failed"):
                if self.default_channel and self.default_channel != info["channel"]:
                    self._post(self.default_channel, msg)
                # Don't del yet — might get follow-up events
        else:
            self._broadcast(msg)

    def _on_multi_notification(self, notification: dict[str, Any]) -> None:
        """Handle notifications from MultiOrchid coordinator."""
        from orchid.interfaces.multi_formatter import format_notification as fmt_multi
        event = notification.get("event", "")
        project = notification.get("project", "")
        data = notification.get("data", {})
        msg = fmt_multi(event, project, data)
        if msg:
            self._broadcast(msg)

    # ── Slash command handlers ─────────────────────────────────────────────────

    def _handle_status(self, ack, say, command) -> None:
        ack()
        if self.multi_project and len(self._all_projects) > 1:
            self._handle_status_multi(say)
            return
        from orchid.interfaces.slack_formatter import format_status, format_status_text
        session = self._make_session()
        blocks = format_status(session)
        say(text=format_status_text(session), blocks=blocks)

    def _handle_status_multi(self, say) -> None:
        from orchid.session import Session
        lines = ["*📊 Multi-project status*", ""]
        for proj_path in self._all_projects:
            try:
                s = Session(project_dir=proj_path)
                s.load()
                from orchid.memory.state import TaskStatus
                todo = sum(1 for t in s.tasks if t.status == TaskStatus.TODO)
                done = sum(1 for t in s.tasks if t.status == TaskStatus.DONE)
                inprog = sum(1 for t in s.tasks if t.status == TaskStatus.IN_PROGRESS)
                blocked = sum(1 for t in s.tasks if t.status == TaskStatus.BLOCKED)
                parts = []
                if inprog:
                    parts.append(f"{inprog} running")
                if todo:
                    parts.append(f"{todo} pending")
                if done:
                    parts.append(f"{done} done")
                if blocked:
                    parts.append(f"{blocked} blocked")
                lines.append(f"• *{s.project_name}*: {', '.join(parts) if parts else 'no tasks'}")
            except Exception as exc:
                lines.append(f"• *{Path(proj_path).name}*: error — {exc}")
        say(text="\n".join(lines))

    def _handle_run(self, ack, say, command, client) -> None:
        task_id = (command.get("text") or "").strip().upper()
        if not task_id:
            ack("Usage: /orchid-run <task_id>  e.g. /orchid-run T001")
            return

        if self._runner.is_running():
            ack("⚠️ A task is already running. Wait for it to finish.")
            return

        session = self._make_session()
        task = next((t for t in session.tasks if t.id == task_id), None)
        if task is None:
            ack(f"❌ Task {task_id} not found.")
            return

        ack()
        from orchid.interfaces.slack_formatter import (
            format_task_complete,
            format_task_failed,
            format_task_started,
        )
        channel = command.get("channel_id", self.default_channel)
        self._notify_channels.add(channel)

        ts = self._post(channel, format_task_started(task_id, task.title))
        if ts:
            self._task_threads[task_id] = {"channel": channel, "thread_ts": ts}

        def _cb(tid: str, result: str | None, error: str | None) -> None:
            self._notify_channels.discard(channel)
            if error:
                msg = format_task_failed(tid, error)
            else:
                msg = format_task_complete(tid, result or "")
            thread_info = self._task_threads.pop(tid, None)
            if thread_info:
                self._post(thread_info["channel"], msg, thread_ts=thread_info["thread_ts"])
            else:
                self._post(channel, msg)

        self._runner.run_task(task_id, _cb, self._loop)

    def _handle_auto(self, ack, say, command, client) -> None:
        if self.multi_project and len(self._all_projects) > 1:
            ack()
            self._handle_auto_multi(command)
            return

        if self._runner.is_running():
            ack("⚠️ Already running. Wait for the current run to finish.")
            return

        session = self._make_session()
        from orchid.memory.state import TaskStatus
        pending = [t for t in session.tasks if t.status == TaskStatus.TODO]
        if not pending:
            ack("No pending tasks.")
            return

        ack()
        from orchid.interfaces.slack_formatter import (
            format_auto_summary,
            format_task_complete,
            format_task_failed,
        )
        channel = command.get("channel_id", self.default_channel)
        self._notify_channels.add(channel)
        say(text=f"🚀 Starting auto run — {len(pending)} pending tasks…", channel=channel)

        def _on_task(tid: str, result: str | None, error: str | None) -> None:
            msg = format_task_failed(tid, error) if error else format_task_complete(tid, result or "")
            thread_info = self._task_threads.pop(tid, None)
            if thread_info:
                self._post(thread_info["channel"], msg, thread_ts=thread_info["thread_ts"])
            else:
                self._post(channel, msg)

        def _on_done(done_ids: list[str], failed_ids: list[str]) -> None:
            self._notify_channels.discard(channel)
            self._post(channel, format_auto_summary(done_ids, failed_ids))

        self._runner.run_auto(_on_task, _on_done, self._loop)

    def _handle_auto_multi(self, command) -> None:
        """Start multi-project parallel run from Slack."""
        if self._multi_thread and self._multi_thread.is_alive():
            return

        channel = command.get("channel_id", self.default_channel)
        self._notify_channels.add(channel)
        self._post(channel, f"🚀 Starting multi-project run — {len(self._all_projects)} project(s)…")

        def _run() -> None:
            from orchid.multi import MultiOrchid
            orch = MultiOrchid(
                projects=self._all_projects,
                notification_callback=self._on_multi_notification,
            )
            try:
                orch.start()
            except Exception as exc:
                logger.exception("Multi-project run error: %s", exc)
            finally:
                self._notify_channels.discard(channel)

        self._multi_thread = threading.Thread(target=_run, daemon=True, name="orchid-slack-multi")
        self._multi_thread.start()

    def _handle_add(self, ack, say, command) -> None:
        text = (command.get("text") or "").strip()
        if not text:
            ack("Usage: /orchid-add <task description>")
            return
        ack()
        session = self._make_session()
        from orchid.memory.state import Task, save_tasks
        tid = f"T{len(session.tasks) + 1:03d}"
        t = Task(id=tid, title=text, type="draft", priority=2, description=text)
        session.tasks.append(t)
        save_tasks(session.tasks, self.project_path)
        say(text=f"✅ Added *{tid}*: {text}  `type=draft p2`")

    def _handle_recall(self, ack, say, command) -> None:
        query = (command.get("text") or "").strip()
        if not query:
            ack("Usage: /orchid-recall <query>")
            return
        ack()
        from orchid import config as cfg
        from orchid.interfaces.slack_formatter import format_recall_results
        from orchid.memory.vector import VectorMemory
        cfg.configure_for_project(self.project_path)
        vm = VectorMemory(project_dir=self.project_path)
        if not vm.available:
            say(text="⚠️ Vector memory not available for this project.")
            return
        results = vm.query(query, n=3)
        say(text=f"🔍 *Recall:* {query}\n\n{format_recall_results(results)}")

    def _handle_search(self, ack, say, command) -> None:
        query = (command.get("text") or "").strip()
        if not query:
            ack("Usage: /orchid-search <query>")
            return
        ack()
        from orchid import config as cfg
        from orchid.interfaces.slack_formatter import format_search_results
        from orchid.tools.search import WebSearchTool, reset_backend_cache
        cfg.configure_for_project(self.project_path)
        reset_backend_cache()
        vm = None
        if cfg.get("web_search.embed_results", True) and cfg.get("vector_memory.enabled", True):
            from orchid.memory.vector import VectorMemory
            vm = VectorMemory(project_dir=self.project_path)
        tool = WebSearchTool(vector_memory=vm, project_name=Path(self.project_path).name)
        results = tool.search(query, n=3)
        say(text=f"🌐 *Search:* {query}\n\n{format_search_results(results)}")

    def _handle_inject(self, ack, say, command) -> None:
        text = (command.get("text") or "").strip()
        if not text:
            ack("Usage: /orchid-inject <text>")
            return
        if not self._runner.is_running():
            ack("⚠️ No task is currently running.")
            return
        ack()
        self._runner.inject(text)
        say(text=f"💉 Injected: {text[:100]}")

    def _handle_help(self, ack, say, command) -> None:
        ack()
        say(text=self._help_text())

    def _help_text(self) -> str:
        return (
            "*Orchid Slash Commands*\n\n"
            "*/orchid-status* — task board and hot memory\n"
            "*/orchid-run <task\\_id>* — run a specific task\n"
            "*/orchid-auto* — run all pending tasks autonomously\n"
            "*/orchid-add <description>* — add a new task\n"
            "*/orchid-recall <query>* — search vector memory\n"
            "*/orchid-search <query>* — web search\n"
            "*/orchid-inject <text>* — inject context into running agent\n"
            "*/orchid-help* — this message\n\n"
            "You can also @mention Orchid with natural language."
        )

    # ── Mention handler ────────────────────────────────────────────────────────

    def _handle_mention(self, event, say, client) -> None:
        """Handle @Orchid mentions with intent parsing."""
        text = event.get("text", "")
        channel = event.get("channel", self.default_channel)
        thread_ts = event.get("thread_ts") or event.get("ts")

        # Strip the bot mention prefix (<@UXXXXXXX> ...)
        import re
        text = re.sub(r"<@\w+>", "", text).strip()
        if not text:
            say(text=self._help_text(), thread_ts=thread_ts)
            return

        intent = self._parse_intent(text)
        action = intent.get("intent", "help")
        arg = intent.get("arg", "")

        self._notify_channels.add(channel)

        if action == "status":
            from orchid.interfaces.slack_formatter import format_status, format_status_text
            session = self._make_session()
            blocks = format_status(session)
            say(text=format_status_text(session), blocks=blocks, thread_ts=thread_ts)

        elif action == "run":
            task_id = arg.upper()
            session = self._make_session()
            task = next((t for t in session.tasks if t.id == task_id), None)
            if task is None:
                say(text=f"❌ Task {task_id} not found.", thread_ts=thread_ts)
                return
            from orchid.interfaces.slack_formatter import (
                format_task_complete,
                format_task_failed,
                format_task_started,
            )
            ts = self._post(channel, format_task_started(task_id, task.title), thread_ts=thread_ts)
            if ts:
                self._task_threads[task_id] = {"channel": channel, "thread_ts": ts}

            def _cb(tid, result, error):
                self._notify_channels.discard(channel)
                msg = format_task_failed(tid, error) if error else format_task_complete(tid, result or "")
                ti = self._task_threads.pop(tid, None)
                self._post(channel, msg, thread_ts=ti["thread_ts"] if ti else thread_ts)

            self._runner.run_task(task_id, _cb, self._loop)

        elif action == "add":
            if not arg:
                say(text="Please describe the task to add.", thread_ts=thread_ts)
                return
            session = self._make_session()
            from orchid.memory.state import Task, save_tasks
            tid = f"T{len(session.tasks) + 1:03d}"
            t = Task(id=tid, title=arg, type="draft", priority=2, description=arg)
            session.tasks.append(t)
            save_tasks(session.tasks, self.project_path)
            say(text=f"✅ Added *{tid}*: {arg}  `type=draft p2`", thread_ts=thread_ts)

        elif action == "recall":
            from orchid import config as cfg
            from orchid.interfaces.slack_formatter import format_recall_results
            from orchid.memory.vector import VectorMemory
            cfg.configure_for_project(self.project_path)
            vm = VectorMemory(project_dir=self.project_path)
            results = vm.query(arg, n=3) if vm.available else []
            say(text=f"🔍 *Recall:* {arg}\n\n{format_recall_results(results)}", thread_ts=thread_ts)

        elif action == "search":
            from orchid import config as cfg
            from orchid.interfaces.slack_formatter import format_search_results
            from orchid.tools.search import WebSearchTool, reset_backend_cache
            cfg.configure_for_project(self.project_path)
            reset_backend_cache()
            tool = WebSearchTool(project_name=Path(self.project_path).name)
            results = tool.search(arg, n=3)
            say(text=f"🌐 *Search:* {arg}\n\n{format_search_results(results)}", thread_ts=thread_ts)

        else:
            say(text=self._help_text(), thread_ts=thread_ts)

    def _parse_intent(self, message: str) -> dict[str, Any]:
        """Parse intent from a natural language message.

        Uses simple rule-based matching first, falls back to Claude for ambiguous input.
        Returns {"intent": str, "arg": str}.
        """
        import re

        msg = message.strip().lower()

        # Simple rule-based classification
        if re.match(r"^(status|tasks|what.*(tasks|pending|running))", msg):
            return {"intent": "status", "arg": ""}
        if re.match(r"^(run|execute|start)\s+(t\d+)", msg, re.I):
            m = re.search(r"(t\d+)", msg, re.I)
            return {"intent": "run", "arg": m.group(1).upper() if m else ""}
        if re.match(r"^(add|create|new)\s+(task|a task|an?)\s*", msg):
            arg = re.sub(r"^(add|create|new)\s+(task|a task|an?)\s*(to\s+)?", "", message.strip(), flags=re.I)
            return {"intent": "add", "arg": arg or message}
        if re.match(r"^(recall|remember|memory|find in memory)", msg):
            arg = re.sub(r"^(recall|remember|memory|find in memory)\s*", "", message.strip(), flags=re.I)
            return {"intent": "recall", "arg": arg}
        if re.match(r"^(search|look up|find|google)\s+", msg):
            arg = re.sub(r"^(search|look up|find|google)\s+(for\s+)?", "", message.strip(), flags=re.I)
            return {"intent": "search", "arg": arg}
        if re.match(r"^(help|commands|what can you)", msg):
            return {"intent": "help", "arg": ""}

        # Fall back to Claude for ambiguous input
        try:
            from orchid.tools.models import Message, call
            prompt = (
                "Parse the intent from this Orchid bot message.\n"
                'Return JSON only: {"intent": "status|run|add|recall|search|help", "arg": "..."}\n\n'
                "Examples:\n"
                '- "what tasks are pending" → {"intent": "status", "arg": ""}\n'
                '- "run T001" → {"intent": "run", "arg": "T001"}\n'
                '- "add a task to fix the login page" → {"intent": "add", "arg": "fix the login page"}\n'
                '- "recall session summary" → {"intent": "recall", "arg": "session summary"}\n'
                '- "search for FastAPI best practices" → {"intent": "search", "arg": "FastAPI best practices"}\n\n'
                f"Message: {message}"
            )
            from orchid.providers.registry import get_registry
            _model_key = get_registry().resolve_name(agent_type="base")
            response = call(
                [Message("user", prompt)],
                model_key=_model_key,
                system="Return JSON only. No explanation.",
            )
            # Extract JSON from response
            import re as _re
            json_match = _re.search(r"\{[^}]+\}", response)
            if json_match:
                return json.loads(json_match.group())
        except Exception as exc:
            logger.debug("Intent parsing via Claude failed: %s", exc)

        return {"intent": "help", "arg": ""}
