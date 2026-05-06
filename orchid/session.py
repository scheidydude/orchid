"""Session manager — loads state at startup, compresses and saves at shutdown."""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchid import config as cfg
from orchid.hooks.events import SESSION_END, SESSION_START, HookEvent
from orchid.hooks.registry import HookRegistry
from orchid.memory import state as mem_state
from orchid.memory.decisions import load_decisions
from orchid.memory.vector import VectorMemory

logger = logging.getLogger(__name__)

# Module-level reference to the active session — set by Session.load().
# Allows providers to report stats without a direct session dependency.
_current_session: Session | None = None


def get_current_session() -> Session | None:
    """Return the currently active Session, or None if no session is loaded."""
    return _current_session


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

        self.started_at = datetime.now(UTC)
        self.tasks: list[mem_state.Task] = []
        self.hot_memory: str = ""
        self.extra_context: str = ""
        self.decisions: list[dict[str, Any]] = []
        self.delegations: list[dict[str, Any]] = []
        self._log_path: Path | None = None
        self._live_log_path: Path | None = None
        self._vector: VectorMemory | None = None
        # Local provider KV-cache stats (populated by LocalProvider.complete())
        self.cache_stats: dict[str, int] = {
            "local_fast_evals": 0,
            "local_slow_evals": 0,
            "local_prompt_tokens": 0,
            "local_prompt_ms": 0,
        }
        # Hook registry for session events (T097)
        self._hook_registry = HookRegistry()
        self._load_hooks()

        # RLock (not Lock) — _execute_task may call update_task_status, which acquires
        # this lock, and the exception handler in _execute_task_with_semaphore may also
        # call it on the same thread. RLock allows safe re-entry from the same thread.
        self._lock = threading.RLock()

    def _load_hooks(self) -> None:
        """Load and register hooks from project configuration."""
        try:
            from orchid.hooks.loader import HookLoader
            loader = HookLoader(self.project_dir)
            count = loader.load()
            # Merge loaded hooks into this session's registry
            if loader.registry:
                for event_type, handlers in loader.registry._handlers.items():
                    for handler in handlers:
                        self._hook_registry._handlers[event_type].append(handler)
                logger.info("Loaded %d hook(s) for session", count)
        except Exception as e:
            logger.warning("Failed to load hooks: %s", e)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Read all state from disk."""
        global _current_session  # noqa: PLW0603
        _current_session = self

        # Reset provider cache stats for this session
        try:
            from orchid.providers.anthropic import reset_session_stats
            reset_session_stats()
        except Exception:
            pass

        self.tasks = mem_state.load_tasks(self.project_dir)
        self.hot_memory = mem_state.load_hot_memory(self.project_dir)
        self.decisions = load_decisions(self.project_dir)
        self.extra_context = self._load_context_files()

        log_dir = self.project_dir / cfg.get("memory.session_log_dir", ".orchid/session_logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        self._log_path = log_dir / f"session_{ts}.jsonl"

        # Live streaming log (human-readable, tailable)
        if cfg.get("streaming.enabled", True):
            self._live_log_path = log_dir / f"session_{ts}.live.log"

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

        # T097: Fire SESSION_START hook
        self._fire_session_start_hook()

    def save(self) -> None:
        """Persist all mutated session state to disk."""
        mem_state.save_tasks(self.tasks, self.project_dir)
        mem_state.save_hot_memory(self.hot_memory, self.project_dir)
        logger.info("Session saved.")

    def close(self, summary: str = "") -> None:
        """Save state, compress hot memory if needed, write session log, embed session."""
        # T097: Fire SESSION_END hook before closing
        self._fire_session_end_hook(summary)
        self._maybe_compress_hot_memory()
        self.save()
        self._write_session_log(summary)
        self._finalize_live_log()
        self._auto_embed_session(summary)

    # T097: Session hook methods
    def _fire_session_start_hook(self) -> None:
        """Fire the SESSION_START hook event."""
        event = HookEvent(
            event_type=SESSION_START,
            data={
                "project_name": self.project_name,
                "project_dir": str(self.project_dir),
                "task_count": len(self.tasks),
                "started_at": self.started_at.isoformat(),
            },
            context={"project": self.project_name},
        )
        self._hook_registry.fire(event)

    def _fire_session_end_hook(self, summary: str) -> None:
        """Fire the SESSION_END hook event."""
        duration = (datetime.now(UTC) - self.started_at).total_seconds()
        event = HookEvent(
            event_type=SESSION_END,
            data={
                "project_name": self.project_name,
                "project_dir": str(self.project_dir),
                "task_count": len(self.tasks),
                "tasks_done": sum(1 for t in self.tasks if t.status == mem_state.TaskStatus.DONE),
                "duration_seconds": duration,
                "summary": summary[:1000] if summary else "",
                "ended_at": datetime.now(UTC).isoformat(),
            },
            context={"project": self.project_name},
        )
        self._hook_registry.fire(event)

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
            from orchid.providers.registry import get_registry
            from orchid.tools.models import Message, call
            model_key = get_registry().resolve_name(agent_type="base")
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
                model_key=model_key,
                system="You are a technical editor. Compress the document faithfully.",
            )
            self.hot_memory = f"<!-- compressed {datetime.now(UTC).date()} -->\n\n{compressed}"
            logger.info("Hot memory compressed to %d chars.", len(self.hot_memory))
        except Exception as e:
            logger.warning("Hot memory compression failed: %s", e)

    # ── Vector memory ─────────────────────────────────────────────────────────

    def recall(self, query: str, n: int | None = None) -> str:
        """Query vector memory and return a formatted context string."""
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
            session_id = self._log_path.stem
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

    # ── Live streaming log ─────────────────────────────────────────────────────

    def stream_react(self, data: dict[str, Any]) -> None:
        """Append a ReAct iteration record to the live streaming log."""
        if not self._live_log_path:
            return
        with self._lock:
            ts = data.get("timestamp", datetime.now(UTC).isoformat())
            iteration = data.get("iter", "?")
            thought = data.get("thought", "").strip()
            action = data.get("action", "").strip()
            observation = data.get("observation", "").strip()

            lines = [f"[{ts}] iter={iteration}"]
            if thought:
                lines.append(f"  Thought: {thought[:300]}")
            if action:
                lines.append(f"  Action:  {action[:200]}")
            if observation:
                lines.append(f"  Obs:     {observation[:300]}")
            lines.append("")

            try:
                with open(self._live_log_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            except Exception as exc:
                logger.debug("stream_react write failed: %s", exc)

    def _finalize_live_log(self) -> None:
        """Rename .live.log → .log to signal completion."""
        if not self._live_log_path or not self._live_log_path.exists():
            return
        name = self._live_log_path.name
        final = self._live_log_path.parent / (name[: -len(".live.log")] + ".log")
        try:
            self._live_log_path.rename(final)
            logger.debug("Live log finalized: %s", final)
        except Exception as exc:
            logger.debug("Failed to finalize live log: %s", exc)

    # ── Session log ────────────────────────────────────────────────────────────

    def record_delegation(self, record: dict[str, Any]) -> None:
        """Record a delegation event in-memory and persist to session log."""
        with self._lock:
            self.delegations.append(record)
            self.log_event("delegation", record)

    def log_event(self, event_type: str, data: dict[str, Any]) -> None:
        with self._lock:
            if not self._log_path:
                return
            record = {
                "ts": datetime.now(UTC).isoformat(),
                "type": event_type,
                **data,
            }
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

    def _write_session_log(self, summary: str) -> None:
        ended_at = datetime.now(UTC)
        duration = (ended_at - self.started_at).total_seconds()

        cache_stats: dict[str, Any] = {}
        try:
            from orchid.providers.anthropic import get_session_stats
            cache_stats = get_session_stats()
            if cache_stats.get("input_tokens_total", 0) > 0:
                logger.info(
                    "Cache stats: %d writes, %d hits, ~%.1f%% token savings",
                    cache_stats["cache_writes"],
                    cache_stats["cache_hits"],
                    cache_stats["estimated_savings_pct"],
                )
        except Exception:
            pass

        event: dict[str, Any] = {
            "summary": summary,
            "duration_seconds": duration,
            "tasks_done": sum(1 for t in self.tasks if t.status == mem_state.TaskStatus.DONE),
            "tasks_total": len(self.tasks),
            "delegations_total": len(self.delegations),
        }
        if cache_stats:
            event["cache_stats"] = cache_stats

        local = self.cache_stats
        local_total = local["local_fast_evals"] + local["local_slow_evals"]
        if local_total > 0:
            pct = local["local_fast_evals"] / local_total * 100
            logger.info(
                "[local cache] %d/%d calls fast (%.0f%% likely cached)",
                local["local_fast_evals"],
                local_total,
                pct,
            )
            event["local_cache_stats"] = dict(local)

        self.log_event("session_end", event)

    # ── Convenience accessors ─────────────────────────────────────────────────

    def next_task(self) -> mem_state.Task | None:
        with self._lock:
            return mem_state.next_task(self.tasks)

    def update_task_status(self, task_id: str, status: mem_state.TaskStatus) -> bool:
        with self._lock:
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
            "\n### Current Tasks\n" + "\n".join(task_lines),
        ]
        if self.extra_context:
            parts.append(f"\n### Additional Context\n{self.extra_context[:2000]}")

        if cfg.get("vector_memory.auto_recall_on_load", True):
            recall_query = " ".join(t.title for t in self.tasks[:5] if t.title)
            if recall_query:
                recalled = self.recall(recall_query)
                if recalled:
                    parts.append(f"\n{recalled}")

        return "\n".join(parts)