"""Tests for orchid.output.events — stream output event dataclasses."""

import json

from orchid.output.events import (
    AgentThoughtEvent,
    SessionEndEvent,
    SessionStartEvent,
    TaskCompleteEvent,
    TaskFailEvent,
    TaskStartEvent,
    ToolResultEvent,
    ToolUseEvent,
)


def test_events_have_correct_type_field():
    """Each event dataclass has the correct 'type' string field."""
    assert SessionStartEvent().type == "session_start"
    assert TaskStartEvent().type == "task_start"
    assert AgentThoughtEvent().type == "agent_thought"
    assert ToolUseEvent().type == "tool_use"
    assert ToolResultEvent().type == "tool_result"
    assert TaskCompleteEvent().type == "task_complete"
    assert TaskFailEvent().type == "task_fail"
    assert SessionEndEvent().type == "session_end"


def test_events_can_be_created_with_only_unique_fields():
    """All fields have defaults — instances can be created with only the unique fields."""
    e1 = SessionStartEvent(session_id="s1", project="/tmp/proj", mode="auto")
    assert e1.session_id == "s1"
    assert e1.project == "/tmp/proj"
    assert e1.mode == "auto"
    assert e1.ts > 0

    e2 = TaskStartEvent(task_id="T001", task_title="Build login", task_type="code_generate")
    assert e2.task_id == "T001"
    assert e2.task_title == "Build login"

    e3 = TaskFailEvent(task_id="T002", error="max iterations")
    assert e3.task_id == "T002"
    assert e3.error == "max iterations"


def test_to_json_returns_valid_json_with_type():
    """to_json() returns a valid JSON string containing the event type."""
    event = SessionStartEvent(session_id="abc", project="/p", mode="auto")
    raw = event.to_json()
    parsed = json.loads(raw)
    assert parsed["type"] == "session_start"
    assert parsed["session_id"] == "abc"
    assert "ts" in parsed

    task_event = TaskCompleteEvent(task_id="T003", duration_s=1.5, iterations=3)
    raw2 = task_event.to_json()
    parsed2 = json.loads(raw2)
    assert parsed2["type"] == "task_complete"
    assert parsed2["task_id"] == "T003"
    assert parsed2["duration_s"] == 1.5
