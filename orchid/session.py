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
from orchid.memory.vector import VectorMemory

logger = logging.getLogger(__name__)


class Session:
    """
    Encapsulates all project state for one orchestrator run.

    project_dir: absolute path to the external project being worked on.
                 Can be any directory — does not need to be under orchid/.
    """

    def __init__(self, project_dir: str | Path = "."):
        self.project_dir = Path(project_dir).resolve()

        # Merge orchid defaults with this project's .orchid.yaml
        self.config = cfg.configure_for_project(self.project_dir)

        # Project metadata from .orchid.yaml (if present)
        project_cfg = cfg.load_project_config(self.project_dir)
        self.project_name: str = project_cfg.get("project") or self.project_dir.name
        self.project_description: str = project_cfg.get("description", "")
        self.context_files: list[str] = project_cfg.get("context_files", [])

        self.started_at = datetime.now(timezone.utc)
        self.tasks: list[mem_state.Task] = []
        self.hot_memory: str = ""
        self.extra_context: str = ""
        self.decisions: list[dict[str, Any]] = []
        self.delegations: list[dict[str, Any]] = []
        self._log_path: Path | None = None
        self._vector: VectorMemory | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Read all state from disk."""
        self.tasks = mem_state.load_tasks(self.project_dir)
        self.hot_memory = mem_state.load_hot_memory(self.project_dir)
        self.decisions = load_decisions(self.project_dir)
        self.extra_context = self._load_context_files()

        log_dir = self.project_dir / cfg.get("memory.session_log_dir", ".orchid/session_logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        self._log_path = log_dir / f"session_{ts}.jsonl"

        # Lazy vector store init (non-fatal if unavailable)
        if cfg.get("vector_memory.enabled", True):
            chunk_size = cfg.get("vector_memory.chunk_size", 512)
            chunk_overlap = cfg.get("vector_memory.chunk_overlap", 64)
            self._vector = VectorMemory(
                project_dir=self.project_dir,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

        logger.info(
            "Session loaded: project=%s tasks=%d decisions=%d delegations=%d hot_memory=%d chars vector=%s",
            self.project_name,
            len(self.tasks),
            len(self.decisions),
            len(self.delegations),
            len(self.hot_memory),
            "on" if (self._vector and self._vector.available) else "off",
        )

    def save(self) -> None:
        """Persist all mutated session state to disk.

        This method is idempotent and can be called multiple times.
        It writes the following to disk:

        - tasks: Updated task list (via mem_state.save_tasks)
        - hot_memory: Current hot memory content (via mem_state.save_hot_memory)

        Side effects:
        - Does NOT write decisions (those are persisted immediately upon creation)
        - Does NOT write session logs (handled by _write_session_log)
        - Does NOT compress hot memory (handled by _maybe_compress_hot_memory)

        Thread safety:
        - Not thread-safe. Callers should ensure single-threaded access.

        Usage:
            Typically called from close() after compression, or periodically
            during long-running sessions to prevent data loss.
        """
        mem_state.save_tasks(self.tasks, self.project_dir)
        mem_state.save_hot_memory(self.hot_memory, self.project_dir)
        logger.info("Session saved.")

    def close(self, summary: str = "") -> None:
        """Save state, compress hot memory if needed, write session log, embed session."""
        self._maybe_compress_hot_memory()
        self.save()
        self._write_session_log(summary)
        self._auto_embed_session(summary)

    # ── Context files ─────────────────────────────────────────────────────────

    def _load_context_files(self) -> str:
        """Load extra context files listed in .orchid.yaml into a single string."""
        parts: list[str] = []
        for rel_path in self.context_files:
            full = self.project_dir / rel_path
            if full.exists():
                parts.append(f"### {rel_path}\n{full.read_text(encoding='utf-8')}")
            else:
                logger.debug("context_file not found: %s", full)
        return "\n\n".join(parts)

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

    # ── Vector memory ─────────────────────────────────────────────────────────

    def recall(self, query: str, n: int | None = None) -> str:
        """
        Query vector memory and return a formatted context string.

        Returns empty string when vector memory is unavailable or empty.
        """
        if not self._vector or not self._vector.available:
            return ""
        n_results = n or cfg.get("vector_memory.n_results", 5)
        results = self._vector.query(query, n=n_results)
        if not results:
            return ""
        lines = ["## Recalled Context", ""]
        for i, r in enumerate(results, 1):
            meta = r["metadata"]
            rtype = meta.get("type", "note")
            ts = meta.get("timestamp", "")[:10]
            sid = meta.get("session_id", "")
            dist = r["distance"]
            header = f"### [{i}] type={rtype}"
            if sid:
                header += f"  session={sid}"
            if ts:
                header += f"  date={ts}"
            header += f"  score={1 - dist:.3f}"
            lines.append(header)
            lines.append(r["text"])
            lines.append("")
        return "\n".join(lines)

    def _auto_embed_session(self, summary: str) -> None:
        """Embed the session log into vector store at session end."""
        if not cfg.get("vector_memory.auto_embed_on_save", True):
            return
        if not self._vector or not self._vector.available:
            return
        if not self._log_path or not self._log_path.exists():
            return

        try:
            log_text = self._log_path.read_text(encoding="utf-8").strip()
            if not log_text:
                return
            session_id = self._log_path.stem  # e.g. "session_20260314_120000"
            self._vector.add_session_log(
                session_id=session_id,
                log_text=log_text,
                metadata={
                    "project": self.project_name,
                    "summary": summary[:500],
                },
            )
            logger.info("Session log embedded into vector store (%s).", session_id)
        except Exception as exc:
            logger.warning("Auto-embed of session log failed: %s", exc)

    # ── Session log ────────────────────────────────────────────────────────────

    def record_delegation(self, record: dict[str, Any]) -> None:
        """Record a delegation event in-memory and persist to session log."""
        self.delegations.append(record)
        self.log_event("delegation", record)

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
            "delegations_total": len(self.delegations),
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
        parts = [
            f"## Project: {self.project_name}",
        ]
        if self.project_description:
            parts.append(self.project_description)
        parts += [
            f"\n### Hot Memory\n{self.hot_memory[:2000]}",
            f"\n### Current Tasks\n" + "\n".join(task_lines),
        ]
        if self.extra_context:
            parts.append(f"\n### Additional Context\n{self.extra_context[:2000]}")

        # Auto-recall: seed context with semantically relevant past sessions
        if cfg.get("vector_memory.auto_recall_on_load", True):
            recall_query = " ".join(t.title for t in self.tasks[:5] if t.title)
            if recall_query:
                recalled = self.recall(recall_query)
                if recalled:
                    parts.append(f"\n{recalled}")

        return "\n".join(parts)
