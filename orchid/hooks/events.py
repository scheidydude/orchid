"""Hook event constants and data classes for Orchid V2.

Defines all hook event types that can be fired during the agent lifecycle,
task lifecycle, and session/phase transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Event Type Constants ─────────────────────────────────────────────────────

# Agent ReAct loop events (T095)
AGENT_ITER_START = "agent_iter_start"
AGENT_ITER_END = "agent_iter_end"
AGENT_ACTION = "agent_action"
AGENT_OBSERVATION = "agent_observation"
AGENT_THOUGHT = "agent_thought"
AGENT_FINAL_ANSWER = "agent_final_answer"

# Task lifecycle events (T096)
TASK_START = "task_start"
TASK_END = "task_end"
TASK_COMPLETE = "task_complete"
TASK_FAILED = "task_failed"
TASK_BLOCKED = "task_blocked"
TASK_SKIPPED = "task_skipped"
TASK_STATUS_CHANGE = "task_status_change"

# Session and phase transition events (T097)
SESSION_START = "session_start"
SESSION_END = "session_end"
PHASE_TRANSITION = "phase_transition"
PHASE_ENTER = "phase_enter"
PHASE_EXIT = "phase_exit"

# Hook system events
HOOK_REGISTERED = "hook_registered"
HOOK_UNREGISTERED = "hook_unregistered"
HOOK_ERROR = "hook_error"


@dataclass
class HookEvent:
    """Represents a hook event fired during Orchid execution.

    Attributes:
        event_type: One of the event type constants above
        data: Arbitrary payload data for the event
        context: Execution context (task_id, phase, etc.)
        timestamp: ISO timestamp when event was fired
    """
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            from datetime import datetime, UTC
            self.timestamp = datetime.now(UTC).isoformat()

    @classmethod
    def task_start_event(cls, task_id: str, title: str, **extra) -> HookEvent:
        """Create a TASK_START event."""
        return cls(
            event_type=TASK_START,
            data={"task_id": task_id, "title": title, **extra},
            context={"task_id": task_id},
        )

    @classmethod
    def task_complete_event(cls, task_id: str, result: str, files: list[str] | None = None, **extra) -> HookEvent:
        """Create a TASK_COMPLETE event."""
        return cls(
            event_type=TASK_COMPLETE,
            data={
                "task_id": task_id,
                "result": result,
                "files_written": files or [],
                **extra
            },
            context={"task_id": task_id},
        )

    @classmethod
    def task_failed_event(cls, task_id: str, error: str, **extra) -> HookEvent:
        """Create a TASK_FAILED event."""
        return cls(
            event_type=TASK_FAILED,
            data={"task_id": task_id, "error": error, **extra},
            context={"task_id": task_id},
        )

    @classmethod
    def phase_transition_event(
        cls,
        from_phase: str,
        to_phase: str,
        project_name: str = "",
        **extra
    ) -> HookEvent:
        """Create a PHASE_TRANSITION event."""
        return cls(
            event_type=PHASE_TRANSITION,
            data={
                "from_phase": from_phase,
                "to_phase": to_phase,
                "project_name": project_name,
                **extra
            },
            context={"phase": to_phase},
        )

    @classmethod
    def agent_action_event(
        cls,
        task_id: str,
        action: str,
        input_data: str,
        iteration: int,
        **extra
    ) -> HookEvent:
        """Create an AGENT_ACTION event."""
        return cls(
            event_type=AGENT_ACTION,
            data={
                "task_id": task_id,
                "action": action,
                "input": input_data,
                "iteration": iteration,
                **extra
            },
            context={"task_id": task_id, "action": action},
        )

    @classmethod
    def agent_observation_event(
        cls,
        task_id: str,
        action: str,
        observation: str,
        error: bool = False,
        **extra
    ) -> HookEvent:
        """Create an AGENT_OBSERVATION event."""
        return cls(
            event_type=AGENT_OBSERVATION,
            data={
                "task_id": task_id,
                "action": action,
                "observation": observation,
                "error": error,
                **extra
            },
            context={"task_id": task_id, "action": action},
        )