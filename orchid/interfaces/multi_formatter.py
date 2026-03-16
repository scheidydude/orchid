"""Multi-project Telegram formatters.

Adds project tags to all notification messages when running in multi-project
mode so users can distinguish which project each message comes from.
"""

from __future__ import annotations

from typing import Any

_MAX_LEN = 4000
_MAX_TAG = 10  # project name truncated to this length in tags


def tag_message(project_name: str, message: str) -> str:
    """Prefix message with [project] tag (project name truncated to 10 chars)."""
    tag = project_name[:_MAX_TAG]
    return f"[{tag}] {message}"


def format_multi_status(statuses: dict[str, Any]) -> str:
    """Format aggregate process-level status across all projects for Telegram."""
    lines = ["📊 Multi-project status", ""]
    if not statuses:
        lines.append("No projects running.")
    else:
        for project_name, info in statuses.items():
            alive = info.get("alive", False)
            restarts = info.get("restarts", 0)
            pid = info.get("pid")
            icon = "🟢" if alive else "⭕"
            line = f"{icon} {project_name}"
            if pid:
                line += f"  pid={pid}"
            if restarts > 0:
                line += f"  [{restarts} restart(s)]"
            lines.append(line)
    return "\n".join(lines)[:_MAX_LEN]


def format_worker_crash(project: str, restart_count: int, max_restarts: int) -> str:
    """Format a worker crash/restart notification."""
    return f"❌ [{project[:_MAX_TAG]}] Worker crashed — restarting ({restart_count}/{max_restarts})"


def format_worker_complete(project: str, tasks_done: int, duration: float | None = None) -> str:
    """Format a worker completion notification."""
    msg = f"✅ [{project[:_MAX_TAG]}] Complete — {tasks_done} task(s) done"
    if duration is not None:
        mins = int(duration // 60)
        secs = int(duration % 60)
        msg += f" in {mins}m {secs}s"
    return msg


def format_worker_failed(project: str, max_restarts: int) -> str:
    """Format a permanent worker failure notification."""
    return f"🔴 [{project[:_MAX_TAG]}] Worker failed permanently after {max_restarts} restart(s)"


def format_notification(event: str, project: str, data: dict[str, Any]) -> str | None:
    """Format a multi-project notification event with project tag.

    Handles worker lifecycle events directly; delegates standard session/task
    events to the single-project formatter and prefixes with the project tag.

    Returns None if the event should not be displayed.
    """
    from orchid.interfaces.telegram_formatter import format_notification as _single

    if event == "worker_restart":
        return format_worker_crash(
            project,
            data.get("restart_count", 1),
            data.get("max_restarts", 3),
        )

    if event == "worker_failed":
        return format_worker_failed(project, data.get("max_restarts", 3))

    if event == "session_complete":
        return format_worker_complete(project, data.get("tasks_done", 0))

    # Standard events — delegate then tag
    msg = _single(event, {**data, "project": project})
    if msg is None:
        return None
    return tag_message(project, msg)
