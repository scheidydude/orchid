# Orchid Cron — Task Run Store (append-only JSONL with 30-day pruning)

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchid.cron.types import TaskRun

logger = logging.getLogger(__name__)

_TASK_RUN_FIELDS = {f.name for f in dataclasses.fields(TaskRun)}
_RETENTION_DAYS = 30


class TaskRunStore:
    """Manages ``~/.config/orchid/cron/runs.jsonl`` — one JSON object per line.
    Thread-safe via an internal lock."""

    def __init__(self, runs_file: Path | None = None) -> None:
        self._file = (
            runs_file
            or Path.home() / ".config" / "orchid" / "cron" / "runs.jsonl"
        )
        self._lock = threading.Lock()
        self._prune()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune(self) -> None:
        """Remove runs older than 30 days."""
        if not self._file.exists():
            return
        try:
            cutoff = datetime.now(UTC) - timedelta(days=_RETENTION_DAYS)
            kept_lines: list[str] = []
            with open(self._file, "r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                        started_at_str = obj.get("started_at")
                        if started_at_str is not None:
                            started_at = datetime.fromisoformat(started_at_str)
                            if started_at.tzinfo is None:
                                started_at = started_at.replace(tzinfo=UTC)
                            if started_at >= cutoff:
                                kept_lines.append(stripped)
                        else:
                            # No timestamp — keep it (safety net)
                            kept_lines.append(stripped)
                    except (json.JSONDecodeError, ValueError):
                        # Unparseable line — keep to avoid data loss
                        kept_lines.append(stripped)

            with open(self._file, "w", encoding="utf-8") as fh:
                for line in kept_lines:
                    fh.write(line + "\n")
        except Exception:
            logger.warning("prune failed — continuing anyway")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, run: TaskRun) -> None:
        """Write one ``TaskRun`` as a JSON line (append mode)."""
        with self._lock:
            try:
                self._file.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(dataclasses.asdict(run), default=str) + "\n"
                with open(self._file, "a", encoding="utf-8") as fh:
                    fh.write(payload)
            except Exception:
                logger.error("append failed for run %s", run.run_id)

    def get_runs(
        self,
        task_id: str = "",
        owner_id: str = "",
        limit: int = 50,
    ) -> list[TaskRun]:
        """Read all lines, filter by *task_id* / *owner_id*, newest-first."""
        if not self._file.exists():
            return []

        try:
            with open(self._file, "r", encoding="utf-8") as fh:
                raw_lines = [l.strip() for l in fh if l.strip()]
        except Exception:
            logger.warning("get_runs failed to read %s", self._file)
            return []

        results: list[TaskRun] = []
        for line in raw_lines:
            try:
                obj = json.loads(line)

                # Parse datetime fields (set to None on failure)
                started_at_str = obj.get("started_at")
                if started_at_str is not None:
                    try:
                        obj["started_at"] = datetime.fromisoformat(started_at_str)
                    except (ValueError, TypeError):
                        obj["started_at"] = None

                finished_at_str = obj.get("finished_at")
                if finished_at_str is not None:
                    try:
                        obj["finished_at"] = datetime.fromisoformat(finished_at_str)
                    except (ValueError, TypeError):
                        obj["finished_at"] = None

                # Filter keys to known fields only
                filtered = {k: v for k, v in obj.items() if k in _TASK_RUN_FIELDS}
                results.append(TaskRun(**filtered))
            except Exception:
                continue

        # Apply filters (empty string means "no filter")
        if task_id:
            results = [r for r in results if r.task_id == task_id]
        if owner_id:
            results = [r for r in results if r.owner_id == owner_id]

        # Sort newest-first by started_at (None -> epoch)
        def _sort_key(r: TaskRun) -> datetime:
            sa = r.started_at
            if sa is None or sa.tzinfo is None:
                return datetime.min.replace(tzinfo=UTC)
            return sa

        results.sort(key=_sort_key, reverse=True)

        # Dedup by run_id — keep only the latest entry per run_id (final status
        # overwrites the initial "running" entry written at task start).
        seen: set[str] = set()
        deduped: list[TaskRun] = []
        for r in results:
            if r.run_id not in seen:
                seen.add(r.run_id)
                deduped.append(r)
        results = deduped

        return results[:limit]
