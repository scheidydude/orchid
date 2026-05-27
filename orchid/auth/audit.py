"""Append-only audit log for Orchid.

Events are written as JSONL (one JSON object per line) to daily files:
  ~/.config/orchid/audit/audit-YYYY-MM-DD.jsonl

Old files are archived forever — the delete endpoint does not exist even for admins.
Thread-safe: a single lock serialises all writes within a process.

For production deployments with high event volume, replace with a database-backed
store. The AuditStore interface is intentionally minimal to make that easy.
"""
import dataclasses
import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from orchid.auth.types import AuditEvent

logger = logging.getLogger(__name__)


class AuditAction:
    """Canonical action strings used in AuditEvent.action."""
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    REGISTER = "register"
    TOKEN_REFRESHED = "token_refreshed"
    API_KEY_CREATED = "api_key_created"
    API_KEY_REVOKED = "api_key_revoked"
    OAUTH_LOGIN = "oauth_login"
    TASK_RUN = "task_run"
    PROJECT_ACCESS_DENIED = "project_access_denied"
    USER_UPDATED = "user_updated"
    USER_DEACTIVATED = "user_deactivated"
    SCHEDULED_TASK_RUN = "scheduled_task_run"
    SCHEDULED_TASK_FAILED = "scheduled_task_failed"
    INVITE_SENT = "invite_sent"
    INVITE_ACCEPTED = "invite_accepted"
    CREDENTIAL_UPDATED = "credential_updated"
    CREDENTIAL_DELETED = "credential_deleted"
    NOTIFICATION_CONFIG_UPDATED = "notification_config_updated"


def make_event(
    user_id: str,
    action: str,
    resource: str,
    result: str,
    ip: str = "",
    detail: str = "",
) -> AuditEvent:
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        user_id=user_id,
        action=action,
        resource=resource,
        result=result,
        timestamp=datetime.now(UTC),
        ip=ip,
        detail=detail,
    )


_AUDIT_EVENT_FIELDS = {f.name for f in dataclasses.fields(AuditEvent)}


class AuditStore:
    """Append-only, daily-rotated JSONL audit log.

    Args:
        audit_dir: Directory for audit files. Defaults to
            ~/.config/orchid/audit/
    """

    def __init__(self, audit_dir: Path | None = None) -> None:
        self._dir = audit_dir or Path.home() / ".config" / "orchid" / "audit"
        self._lock = threading.Lock()

    def _current_file(self) -> Path:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return self._dir / f"audit-{today}.jsonl"

    def log(self, event: AuditEvent) -> None:
        """Write one event to the current day's log file. Never raises."""
        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                line = json.dumps(dataclasses.asdict(event), default=str) + "\n"
                with open(self._current_file(), "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as exc:
            logger.error("Audit log write failed: %s", exc)

    def read(
        self,
        limit: int = 100,
        offset: int = 0,
        user_id: str = "",
        action: str = "",
    ) -> tuple[list[AuditEvent], int]:
        """Read audit events, newest first. Returns (page, total_matching).

        Args:
            limit: Max events to return per page (capped at 500 by caller).
            offset: Number of matching events to skip.
            user_id: Filter to this user only (empty = all users).
            action: Filter to this action only (empty = all actions).
        """
        if not self._dir.exists():
            return [], 0

        all_events: list[AuditEvent] = []

        for path in sorted(self._dir.glob("audit-*.jsonl"), reverse=True):
            try:
                with open(path, encoding="utf-8") as f:
                    for raw in f:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                            if user_id and data.get("user_id") != user_id:
                                continue
                            if action and data.get("action") != action:
                                continue
                            if isinstance(data.get("timestamp"), str):
                                data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                            filtered = {k: v for k, v in data.items() if k in _AUDIT_EVENT_FIELDS}
                            all_events.append(AuditEvent(**filtered))
                        except Exception:
                            continue
            except Exception as exc:
                logger.warning("Failed to read audit file %s: %s", path, exc)

        total = len(all_events)
        return all_events[offset: offset + limit], total
