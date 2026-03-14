"""Tests for memory/decisions."""

from __future__ import annotations

from orchid.memory.decisions import record_decision, load_decisions, recent_decisions


def test_record_and_load(tmp_path):
    rec = record_decision(
        title="Use llama.cpp for worker tasks",
        decision="Route all draft/summarize tasks to local llama.cpp",
        rationale="Cost and latency",
        project_dir=tmp_path,
    )
    assert rec["id"] == "D0001"
    decisions = load_decisions(tmp_path)
    assert len(decisions) == 1
    assert decisions[0]["title"] == "Use llama.cpp for worker tasks"


def test_multiple_decisions(tmp_path):
    for i in range(5):
        record_decision(f"Decision {i}", f"Do thing {i}", project_dir=tmp_path)
    all_decisions = load_decisions(tmp_path)
    assert len(all_decisions) == 5
    recent = recent_decisions(n=3, project_dir=tmp_path)
    assert len(recent) == 3
    assert recent[-1]["title"] == "Decision 4"
