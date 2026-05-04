"""Test that the CLI accepts ``--output-format stream-json`` and emits NDJSON events.

Uses ``subprocess.run`` to invoke the real ``orchid`` entry-point so the full
CLI pipeline (typer dispatch → Orchestrator → NDJSONEmitter) is exercised.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PYTHON = str(Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python")


def _make_minimal_project(tmp_path: Path) -> Path:
    """Create a tiny orchid project with one pending task."""
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n- [ ] **T001** Write a hello world function `type:draft` `p1`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Test Project\n", encoding="utf-8")
    return tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="subprocess test is POSIX only")
def test_stream_json_cli_emits_ndjson_events(tmp_path: Path) -> None:
    """Invoke the CLI with ``--output-format stream-json`` and verify NDJSON output."""
    project = _make_minimal_project(tmp_path)

    result = subprocess.run(
        [
            _PYTHON, "-m", "orchid.interfaces.cli",
            "--project", str(project),
            "--mode", "auto",
            "--output-format", "stream-json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, f"CLI exited with {result.returncode}: {result.stderr[:500]}"

    ndjson_events: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "type" in obj:
            ndjson_events.append(obj)

    assert len(ndjson_events) >= 1, (
        f"Expected at least one NDJSON event in stdout, got {len(ndjson_events)}. "
        f"First 200 chars of stdout: {result.stdout[:200]}"
    )

    event_types = {ev["type"] for ev in ndjson_events}
    assert "task_start" in event_types, (
        f"Expected 'task_start' event in NDJSON output. Got types: {event_types}"
    )
    assert "task_complete" in event_types, (
        f"Expected 'task_complete' event in NDJSON output. Got types: {event_types}"
    )

    task_start = next(ev for ev in ndjson_events if ev["type"] == "task_start")
    assert task_start["task_id"] == "T001"
    assert task_start["task_title"] == "Write a hello world function"
    assert task_start["task_type"] == "draft"
    assert "session_id" in task_start
    assert "ts" in task_start
