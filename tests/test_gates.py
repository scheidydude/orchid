"""Tests for orchid/gates.py — GateSystem."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from orchid.lifecycle import ProjectLifecycle
from orchid.gates import GateSystem, GateStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".orchid").mkdir()
    return tmp_path


@pytest.fixture
def lc_discussing(proj):
    lc = ProjectLifecycle.load(proj)
    lc.advance("DISCUSSING")
    return lc


# ── check_gate: BLOCKED when prerequisites missing ────────────────────────────


def test_gate_blocked_when_artifacts_missing(lc_discussing, proj):
    gates = GateSystem(lc_discussing)
    # PLANNING requires REQUIREMENTS.md
    status = gates.check_gate("REQUIREMENTS")
    # REQUIREMENTS has no prereqs — should be WAITING (human gate)
    assert status == GateStatus.WAITING


def test_gate_blocked_planning_without_requirements(lc_discussing, proj):
    # advance to REQUIREMENTS phase first
    lc_discussing.advance("REQUIREMENTS")
    gates = GateSystem(lc_discussing)
    # PLANNING needs REQUIREMENTS.md
    status = gates.check_gate("PLANNING")
    assert status == GateStatus.BLOCKED


def test_gate_open_when_artifacts_present_and_approved(lc_discussing, proj):
    # Create the needed artifact
    (proj / "REQUIREMENTS.md").write_text("# Req")
    lc_discussing.advance("REQUIREMENTS")
    gates = GateSystem(lc_discussing)

    # Approve the gate
    gates.approve("PLANNING")
    status = gates.check_gate("PLANNING")
    assert status == GateStatus.OPEN


# ── check_gate: auto gates ─────────────────────────────────────────────────────


def test_gate_auto_opens_automatically(lc_discussing):
    # Force discussing→requirements to auto
    key = "discussing_to_requirements"
    lc_discussing.state.gates[key] = {"type": "auto"}
    gates = GateSystem(lc_discussing)
    status = gates.check_gate("REQUIREMENTS")
    assert status == GateStatus.OPEN


def test_gate_human_requires_approval(lc_discussing):
    # Default is human; no approval recorded
    gates = GateSystem(lc_discussing)
    status = gates.check_gate("REQUIREMENTS")
    assert status == GateStatus.WAITING


# ── approve ───────────────────────────────────────────────────────────────────


def test_gate_approval_recorded(lc_discussing, proj):
    gates = GateSystem(lc_discussing)
    gates.approve("REQUIREMENTS")

    lc2 = ProjectLifecycle.load(proj)
    key = "discussing_to_requirements"
    assert lc2.state.gates[key]["approved"] is True


def test_gate_approval_includes_timestamp(lc_discussing):
    gates = GateSystem(lc_discussing)
    gates.approve("REQUIREMENTS", approver="ci-bot")
    key = "discussing_to_requirements"
    gate_data = lc_discussing.state.gates[key]
    assert "approved_at" in gate_data
    assert gate_data["approver"] == "ci-bot"


def test_gate_open_after_approval(lc_discussing):
    gates = GateSystem(lc_discussing)
    assert gates.check_gate("REQUIREMENTS") == GateStatus.WAITING
    gates.approve("REQUIREMENTS")
    assert gates.check_gate("REQUIREMENTS") == GateStatus.OPEN


# ── Gate type resolution ──────────────────────────────────────────────────────


def test_gate_type_from_state_overrides_defaults(lc_discussing):
    key = "discussing_to_requirements"
    lc_discussing.state.gates[key] = {"type": "auto"}
    gates = GateSystem(lc_discussing)
    assert gates._resolve_gate_type(key) == "auto"


def test_gate_type_defaults_to_human(lc_discussing):
    gates = GateSystem(lc_discussing)
    # No gate config at all → should default to "human"
    assert gates._resolve_gate_type("unknown_transition") == "human"


# ── notify_gate_reached ───────────────────────────────────────────────────────


def test_notify_gate_reached_logs(lc_discussing, caplog):
    import logging
    gates = GateSystem(lc_discussing)
    with caplog.at_level(logging.INFO, logger="orchid.gates"):
        gates.notify_gate_reached("REQUIREMENTS")
    assert any("Gate reached" in r.message for r in caplog.records)
