"""Tests for the `orchid decide` CLI subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from typer.testing import CliRunner

from orchid.interfaces.cli import app

runner = CliRunner()


def test_decide_creates_decision(tmp_path):
    result = runner.invoke(app, [
        "decide", "Use PostgreSQL",
        "--decision", "We will use PostgreSQL as the primary database",
        "--rationale", "Needs concurrent writes and row-level locking",
        "--project", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    assert "D0001" in result.output
    assert "Use PostgreSQL" in result.output


def test_decide_writes_decisions_json(tmp_path):
    runner.invoke(app, [
        "decide", "Use PostgreSQL",
        "--decision", "PostgreSQL for primary DB",
        "--project", str(tmp_path),
    ])
    decisions_file = tmp_path / ".orchid" / "decisions.json"
    assert decisions_file.exists()
    record = json.loads(decisions_file.read_text())
    assert record["id"] == "D0001"
    assert record["title"] == "Use PostgreSQL"
    assert record["decision"] == "PostgreSQL for primary DB"


def test_decide_rationale_optional(tmp_path):
    result = runner.invoke(app, [
        "decide", "Use local llama.cpp",
        "--decision", "Route draft tasks to local model",
        "--project", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output


def test_decide_increments_id(tmp_path):
    for title, decision in [
        ("Decision A", "Do A"),
        ("Decision B", "Do B"),
        ("Decision C", "Do C"),
    ]:
        runner.invoke(app, [
            "decide", title,
            "--decision", decision,
            "--project", str(tmp_path),
        ])

    decisions_file = tmp_path / ".orchid" / "decisions.json"
    lines = [l for l in decisions_file.read_text().splitlines() if l.strip()]
    records = [json.loads(l) for l in lines]
    ids = [r["id"] for r in records]
    assert ids == ["D0001", "D0002", "D0003"]


def test_decide_missing_decision_flag_errors(tmp_path):
    result = runner.invoke(app, [
        "decide", "Some title",
        "--project", str(tmp_path),
    ])
    assert result.exit_code != 0
