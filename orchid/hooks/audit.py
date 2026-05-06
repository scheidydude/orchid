"""Audit logging for Orchid hook execution.

Records every hook invocation (shell, HTTP, Python) with outcome,
duration, and error information into the project's .orchid/audit_log.jsonl.

Usage:
    from orchid.hooks.audit import audit_hook, AuditLogger

    audit_logger = AuditLogger(project_dir)
    audit_logger.log(event_type, hook_name, "shell", "success", duration_s=0.34)

The audit log is a JSONL file: one JSON object per line.
Each line has: timestamp, event_type, hook_name, hook_type, status,
duration_s, error, task_id, project_dir.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    """A single audit log entry."""
    timestamp: str = ""
    event_type: str = ""
    hook_name: str = ""
    hook_type: str = ""
    status: str = ""          # "success" | "failure" | "blocked" | "timeout" | "error"
    duration_s: float = 0.0
    error: str = ""
    task_id: str = ""
    project_dir: str = ""
    command: str = ""         # For shell hooks: the executed command
    status_code: int = 0      # For HTTP hooks: response status code

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "hook_name": self.hook_name,
            "hook_type": self.hook_type,
            "status": self.status,
            "duration_s": round(self.duration_s, 3),
            "error": self.error,
            "task_id": self.task_id,
            "project_dir": self.project_dir,
            "command": self.command,
            "status_code": self.status_code,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class AuditLogger:
    """Thread-safe JSONL audit logger for hook execution.

    Writes one JSON object per line to .orchid/audit_log.jsonl.
    Uses a per-write lock so concurrent handlers do not interleave lines.
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir).resolve()
        self._log_path = self.project_dir / ".orchid" / "audit_log.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, entry: AuditEntry) -> None:
        """Append a single audit entry to the JSONL file."""
        line = entry.to_json() + "\n"
        with self._lock:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line)

    def log_hook(
        self,
        event_type: str,
        hook_name: str,
        hook_type: str,
        status: str,
        *,
        duration_s: float = 0.0,
        error: str = "",
        task_id: str = "",
        command: str = "",
        status_code: int = 0,
    ) -> None:
        """Convenience: log a hook execution result."""
        entry = AuditEntry(
            event_type=event_type,
            hook_name=hook_name,
            hook_type=hook_type,
            status=status,
            duration_s=duration_s,
            error=error,
            task_id=task_id,
            project_dir=str(self.project_dir),
            command=command,
            status_code=status_code,
        )
        self.log(entry)

    def read_entries(self, limit: int = 100) -> list[AuditEntry]:
        """Read the last *limit* entries from the audit log."""
        if not self._log_path.exists():
            return []
        with open(self._log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entries.append(AuditEntry(**d))
            except json.JSONDecodeError:
                continue
        return entries

    def clear(self) -> None:
        """Truncate the audit log."""
        with self._lock:
            if self._log_path.exists():
                self._log_path.write_text("", encoding="utf-8")


# ── Module-level singleton ───────────────────────────────────────────────────

_logger: AuditLogger | None = None


def _get_logger() -> AuditLogger | None:
    return _logger


def configure_audit_logger(project_dir: Path) -> AuditLogger:
    """Configure the global audit logger for a project."""
    global _logger
    _logger = AuditLogger(project_dir)
    logger.info("Audit logger configured for %s", project_dir)
    return _logger


def get_audit_logger() -> AuditLogger | None:
    """Return the configured global audit logger, or None."""
    return _logger


def audit_hook(
    event_type: str,
    hook_name: str,
    hook_type: str,
    status: str,
    *,
    duration_s: float = 0.0,
    error: str = "",
    task_id: str = "",
    command: str = "",
    status_code: int = 0,
) -> None:
    """Fire-and-forget audit log for a hook execution.

    Uses the global AuditLogger if configured; silently no-ops otherwise.
    """
    logger_obj = _get_logger()
    if logger_obj is None:
        return
    logger_obj.log_hook(
        event_type=event_type,
        hook_name=hook_name,
        hook_type=hook_type,
        status=status,
        duration_s=duration_s,
        error=error,
        task_id=task_id,
        command=command,
        status_code=status_code,
    )