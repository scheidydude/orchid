"""Session manager — loads state at startup, compresses and saves at shutdown."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchid import config as cfg
from orchid.memory import state as mem_state
from orchid.memory.decisions import load_decisions

logger = logging.getLogger(__name__)


class Session:
    """
    Encapsulates all project state for one orchestrator run.

    project_dir: path to the project being worked on.
                 Defaults to cwd. Can be a subdirectory under projects/.
    """

    def __init__(self, project_dir: str | Path = "."):
        self.project_dir = Path(project_dir).resolve()
        self.started_at = datetime.now(timezone.utc)
        self.tasks: list[mem_state.Task] = []
        self.hot_memory: str = ""
        self.decisions: list[dict[str, Any]] = []
        self._log_path: Path | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Read all state from disk."""
        self.tasks = mem_state.load_tasks(self.project_dir)
        self.hot_memory = mem_state.load_hot_memory(self.project_dir)
        self.decisions = load_decisions(self.project_dir)

        log_dir = self.project_dir / cfg.get("memory.session_log_dir", ".orchid/session_logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        self._log_path = log_dir / f"session_{ts}.jsonl"

        logger.info(
            "Session loaded: %d tasks, %d decisions, hot_memory=%d chars",
            len(self.tasks),
            len(self.decisions),
            len(self.hot_memory),
        )

    def save(self) -> None:
        """Persist all mutated state back to disk."""
        mem_state.save_tasks(self.tasks, self.project_dir)
        mem_state.save_hot_memory(self.hot_memory, self.project_dir)
        logger.info("Session saved.")

    def close(self, summary: str = "") -> None:
        """
        Save state and write a session summary to the log.
        Triggers hot memory compression if over threshold.
        """
        self._maybe_compress_hot_memory()
        self.save()
        self._write_session_log(summary)

    # ── Hot memory compression ─────────────────────────────────────────────────

    def _maybe_compress_hot_memory(self) -> None:
        threshold = cfg.get("memory.compression_threshold", 6000)
        if len(self.hot_memory) <= threshold:
            return

        logger.info("Hot memory exceeds %d chars — compressing...", threshold)
        try:
            from orchid.tools.models import call, Message
            compressed = call(
                messages=[
                    Message("user", (
                        "The following is a CLAUDE.md hot memory file for an AI agent project. "
                        "Compress it to the essential facts, decisions, and current state. "
                        "Preserve all task IDs, decision IDs, and critical architectural notes. "
                        "Target: under 3000 characters.\n\n"
                        f"{self.hot_memory}"
                    ))
                ],
                model_key="claude",
                system="You are a technical editor. Compress the document faithfully.",
            )
            self.hot_memory = f"<!-- compressed {datetime.now(timezone.utc).date()} -->\n\n{compressed}"
            logger.info("Hot memory compressed to %d chars.", len(self.hot_memory))
        except Exception as e:
            logger.warning("Hot memory compression failed: %s", e)

    # ── Session log ────────────────────────────────────────────────────────────

    def log_event(self, event_type: str, data: dict[str, Any]) -> None:
        if not self._log_path:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **data,
        }
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def _write_session_log(self, summary: str) -> None:
        ended_at = datetime.now(timezone.utc)
        duration = (ended_at - self.started_at).total_seconds()
        self.log_event("session_end", {
            "summary": summary,
            "duration_seconds": duration,
            "tasks_done": sum(1 for t in self.tasks if t.status == mem_state.TaskStatus.DONE),
            "tasks_total": len(self.tasks),
        })

    # ── Convenience accessors ─────────────────────────────────────────────────

    def next_task(self) -> mem_state.Task | None:
        return mem_state.next_task(self.tasks)

    def update_task_status(self, task_id: str, status: mem_state.TaskStatus) -> bool:
        for task in self.tasks:
            if task.id == task_id:
                task.status = status
                return True
        return False

    def context_block(self) -> str:
        """Return a formatted context string for injecting into agent prompts."""
        task_lines = [t.to_md_line() for t in self.tasks[:20]]
        return (
            f"## Project: {self.project_dir.name}\n\n"
            f"### Hot Memory\n{self.hot_memory[:2000]}\n\n"
            f"### Current Tasks\n" + "\n".join(task_lines)
        )
