from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _new_task_id() -> str:
    return f"stask_{uuid.uuid4().hex[:8]}"


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ScheduledTask:
    task_id: str = field(default_factory=_new_task_id)
    owner_id: str = ""
    name: str = ""
    description: str = ""
    enabled: bool = True
    schedule: str = "0 9 * * *"
    task_type: str = "agent_prompt"
    config: dict = field(default_factory=dict)
    notify_on_failure: bool = True
    notify_on_success: bool = False
    created_at: datetime = field(default_factory=_utcnow)
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    next_run_at: datetime | None = None


@dataclass
class TaskRun:
    run_id: str = field(default_factory=_new_run_id)
    task_id: str = ""
    owner_id: str = ""
    task_name: str = ""
    task_type: str = ""
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None
    status: str = "running"
    output: str = ""
    error: str = ""
