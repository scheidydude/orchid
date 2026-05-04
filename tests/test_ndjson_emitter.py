"""Tests for orchid.output.ndjson_emitter — NDJSONEmitter and NDJSONBufferEmitter."""

import io
import json

from orchid.output.emitter import NullEmitter
from orchid.output.events import SessionStartEvent, TaskStartEvent
from orchid.output.ndjson_emitter import NDJSONBufferEmitter, NDJSONEmitter


def test_ndjson_emitter_writes_lines_to_stream():
    """NDJSONEmitter writes each event as a newline-terminated JSON line."""
    buf = io.StringIO()
    emitter = NDJSONEmitter(out=buf, flush=False)

    emitter.emit(SessionStartEvent(session_id="s1", project="/p", mode="auto"))
    emitter.emit(TaskStartEvent(task_id="T001", task_title="Build", task_type="code_generate"))

    lines = buf.getvalue().splitlines()
    assert len(lines) == 2

    obj0 = json.loads(lines[0])
    assert obj0["type"] == "session_start"
    assert obj0["session_id"] == "s1"

    obj1 = json.loads(lines[1])
    assert obj1["type"] == "task_start"
    assert obj1["task_id"] == "T001"


def test_ndjson_buffer_emitter_collects_in_memory():
    """NDJSONBufferEmitter accumulates events and returns them via get_json_objects()."""
    emitter = NDJSONBufferEmitter()

    emitter.emit(SessionStartEvent(session_id="s2", project="/q", mode="interactive"))
    emitter.emit(TaskStartEvent(task_id="T002"))

    lines = emitter.get_lines()
    assert len(lines) == 2
    assert all(line.endswith("") for line in lines)  # lines are plain JSON strings

    objects = emitter.get_json_objects()
    assert objects[0]["type"] == "session_start"
    assert objects[1]["type"] == "task_start"

    emitter.clear()
    assert emitter.get_lines() == []


def test_null_emitter_is_noop():
    """NullEmitter.emit() and close() complete without error and produce no output."""
    emitter = NullEmitter()
    emitter.emit(SessionStartEvent(session_id="s3"))
    emitter.emit(TaskStartEvent(task_id="T003"))
    emitter.close()
