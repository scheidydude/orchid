"""Example hook configurations for Orchid V2.

This module provides example hook handlers that can be used with the hook system.
Register these handlers programmatically or reference them in .orchid.yaml.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def on_task_start(event: Any) -> None:
    """Example handler for task_start events.
    
    Logs task start to a file and optionally notifies external systems.
    """
    task_id = event.data.get("task_id", "?")
    title = event.data.get("title", "?")
    task_type = event.data.get("type", "?")
    
    logger.info("TASK START: %s - %s [type=%s]", task_id, title, task_type)
    
    # Write to task log
    log_path = Path(".orchid/task_hooks.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[{event.timestamp}] START {task_id}: {title}\n",
        encoding="utf-8",
        mode="a"
    )


def on_task_complete(event: Any) -> None:
    """Example handler for task_complete events.
    
    Logs task completion and files written.
    """
    task_id = event.data.get("task_id", "?")
    title = event.data.get("title", "?")
    files = event.data.get("files_written", [])
    
    logger.info("TASK COMPLETE: %s - %s [files=%d]", task_id, title, len(files))
    
    # Log files written
    if files:
        for f in files:
            logger.debug("  -> %s", f)


def on_task_failed(event: Any) -> None:
    """Example handler for task_failed events.
    
    Logs task failure and error details.
    """
    task_id = event.data.get("task_id", "?")
    title = event.data.get("title", "?")
    error = event.data.get("error", "Unknown error")
    
    logger.error("TASK FAILED: %s - %s [error=%s]", task_id, title, error[:100])


def on_phase_transition(event: Any) -> None:
    """Example handler for phase_transition events.
    
    Logs phase transitions and updates phase markers.
    """
    from_phase = event.data.get("from_phase", "?")
    to_phase = event.data.get("to_phase", "?")
    project = event.data.get("project_name", "?")
    
    logger.info("PHASE TRANSITION: %s → %s [project=%s]", from_phase, to_phase, project)
    
    # Update phase marker file
    marker_path = Path(".orchid/current_phase.txt")
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(to_phase, encoding="utf-8")


def on_session_start(event: Any) -> None:
    """Example handler for session_start events.
    
    Logs session start and project info.
    """
    project = event.data.get("project_name", "?")
    task_count = event.data.get("task_count", 0)
    
    logger.info("SESSION START: %s [tasks=%d]", project, task_count)


def on_session_end(event: Any) -> None:
    """Example handler for session_end events.
    
    Logs session summary and duration.
    """
    project = event.data.get("project_name", "?")
    duration = event.data.get("duration_seconds", 0)
    tasks_done = event.data.get("tasks_done", 0)
    tasks_total = event.data.get("tasks_total", 0)
    
    logger.info(
        "SESSION END: %s [duration=%.1fs, tasks=%d/%d]",
        project, duration, tasks_done, tasks_total
    )