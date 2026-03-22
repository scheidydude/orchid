"""Complex regex parser for extracting structured data from session logs."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionLogEvent:
    """Represents a single parsed event from a session log."""
    timestamp: datetime
    event_type: str
    raw_data: dict[str, Any]
    session_id: str = ""
    task_id: str = ""
    task_title: str = ""
    duration_seconds: float = 0.0
    tasks_done: int = 0
    tasks_total: int = 0
    delegations_total: int = 0
    summary: str = ""
    result: str = ""
    score: float = 0.0
    iter_count: int = 0
    thought: str = ""
    action: str = ""
    observation: str = ""
    agent_type: str = ""
    task_depth: int = 0
    project_name: str = ""

    def __post_init__(self):
        if self.raw_data.get("ts"):
            try:
                self.timestamp = datetime.fromisoformat(self.raw_data["ts"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                self.timestamp = datetime.now(UTC)
