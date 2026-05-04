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
PRE_TOOL_USE = "pre_tool_use"
POST_TOOL_USE = "post_tool_use"
DELEGATION_START = "delegation_start"
DELEGATION_END = "delegation_end"

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
            from datetime import UTC, datetime
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

    @classmethod
    def pre_tool_use_event(cls, tool: str, input_data: Any, task_id: str = "") -> HookEvent:
        """Create a PRE_TOOL_USE event."""
        return cls(
            event_type=PRE_TOOL_USE,
            data={"tool": tool, "input": input_data},
            context={"task_id": task_id, "tool": tool},
        )

    @classmethod
    def post_tool_use_event(cls, tool: str, input_data: Any, output: str, task_id: str = "") -> HookEvent:
        """Create a POST_TOOL_USE event."""
        return cls(
            event_type=POST_TOOL_USE,
            data={"tool": tool, "input": input_data, "output": output},
            context={"task_id": task_id, "tool": tool},
        )

    @classmethod
    def delegation_start_event(cls, agent_type: str, task: str, depth: int, task_id: str = "") -> HookEvent:
        """Create a DELEGATION_START event."""
        return cls(
            event_type=DELEGATION_START,
            data={"agent_type": agent_type, "task": task, "depth": depth},
            context={"task_id": task_id},
        )

    @classmethod
    def delegation_end_event(cls, agent_type: str, result: str, depth: int, task_id: str = "") -> HookEvent:
        """Create a DELEGATION_END event."""
        return cls(
            event_type=DELEGATION_END,
            data={"agent_type": agent_type, "result": result, "depth": depth},
            context={"task_id": task_id},
        )


# ── Typed HookContext dataclasses ─────────────────────────────────────────────

@dataclass
class PreToolUseContext:
    task_id: str = ""
    tool: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class PostToolUseContext:
    task_id: str = ""
    tool: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    output: str = ""


@dataclass
class TaskStartContext:
    task_id: str = ""
    title: str = ""
    task_type: str = ""
    model: str = ""


@dataclass
class TaskEndContext:
    task_id: str = ""
    status: str = ""
    duration_s: float = 0.0
    iterations: int = 0
    error: str = ""


@dataclass
class SessionStartContext:
    project: str = ""
    mode: str = ""
    provider: str = ""


@dataclass
class SessionEndContext:
    task_count: int = 0
    duration_s: float = 0.0


@dataclass
class PhaseTransitionContext:
    from_phase: str = ""
    to_phase: str = ""
    project_name: str = ""


@dataclass
class DelegationContext:
    agent_type: str = ""
    task: str = ""
    depth: int = 0
    task_id: str = ""