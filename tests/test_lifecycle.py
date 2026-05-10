"""Tests for orchid/lifecycle.py — ProjectLifecycle state machine."""

import json

import pytest

from orchid.lifecycle import ProjectLifecycle

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def proj(tmp_path):
    """Return a temp project dir with .orchid/ created."""
    (tmp_path / ".orchid").mkdir()
    return tmp_path


# ── Initial state ─────────────────────────────────────────────────────────────


def test_initial_phase_is_new(proj):
    lc = ProjectLifecycle.load(proj)
    assert lc.current_phase() == "NEW"


def test_state_created_with_project_name(proj):
    lc = ProjectLifecycle.load(proj)
    assert lc.state.project_name == proj.name


# ── Transitions ───────────────────────────────────────────────────────────────


def test_valid_transitions(proj):
    lc = ProjectLifecycle.load(proj)
    lc.advance("DISCUSSING")
    assert lc.current_phase() == "DISCUSSING"

    lc.advance("REQUIREMENTS")
    assert lc.current_phase() == "REQUIREMENTS"

    lc.advance("PLANNING")
    lc.advance("READY")
    lc.advance("EXECUTING")
    lc.advance("COMPLETE")
    assert lc.current_phase() == "COMPLETE"


def test_any_phase_can_return_to_discussing(proj):
    lc = ProjectLifecycle.load(proj)
    # Move forward then back to DISCUSSING
    lc.advance("DISCUSSING")
    lc.advance("REQUIREMENTS")
    lc.advance("DISCUSSING")
    assert lc.current_phase() == "DISCUSSING"


def test_executing_can_replan(proj):
    lc = ProjectLifecycle.load(proj)
    lc.advance("DISCUSSING")
    lc.advance("REQUIREMENTS")
    lc.advance("PLANNING")
    lc.advance("READY")
    lc.advance("EXECUTING")
    lc.advance("PLANNING")
    assert lc.current_phase() == "PLANNING"


def test_complete_can_replan(proj):
    lc = ProjectLifecycle.load(proj)
    for p in ["DISCUSSING", "REQUIREMENTS", "PLANNING", "READY", "EXECUTING", "COMPLETE"]:
        lc.advance(p)
    lc.advance("PLANNING")
    assert lc.current_phase() == "PLANNING"


def test_invalid_transition_raises(proj):
    lc = ProjectLifecycle.load(proj)
    with pytest.raises(ValueError, match="Invalid transition"):
        lc.advance("EXECUTING")  # can't jump from NEW to EXECUTING


def test_invalid_transition_from_new_raises(proj):
    lc = ProjectLifecycle.load(proj)
    with pytest.raises(ValueError):
        lc.advance("COMPLETE")


# ── Persistence ───────────────────────────────────────────────────────────────


def test_state_persists_to_json(proj):
    lc = ProjectLifecycle.load(proj)
    lc.advance("DISCUSSING")
    lc.save()

    state_file = proj / ".orchid" / "project.state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data["phase"] == "DISCUSSING"


def test_state_loads_from_json(proj):
    state_file = proj / ".orchid" / "project.state.json"
    state_file.write_text(json.dumps({
        "phase": "PLANNING",
        "project_name": "myproject",
        "created_at": "2026-01-01T00:00:00+00:00",
        "last_activity": "2026-01-01T00:00:00+00:00",
        "current_milestone": "M1",
        "gates": {},
        "artifacts": {},
        "slack_channel": None,
        "discussion_turns": 3,
    }))

    lc = ProjectLifecycle.load(proj)
    assert lc.current_phase() == "PLANNING"
    assert lc.state.project_name == "myproject"
    assert lc.state.discussion_turns == 3


def test_state_loads_defaults_for_missing_keys(proj):
    state_file = proj / ".orchid" / "project.state.json"
    state_file.write_text(json.dumps({"phase": "READY"}))
    lc = ProjectLifecycle.load(proj)
    assert lc.current_phase() == "READY"
    assert lc.state.discussion_turns == 0


# ── Gate helpers ──────────────────────────────────────────────────────────────


def test_gate_requires_approval_default_is_human(proj):
    lc = ProjectLifecycle.load(proj)
    lc.advance("DISCUSSING")
    # No approval recorded → should require human
    assert lc.gate_requires_approval("REQUIREMENTS") is True


def test_gate_does_not_require_approval_when_auto(proj):
    lc = ProjectLifecycle.load(proj)
    lc.advance("DISCUSSING")
    key = lc._transition_key("DISCUSSING", "REQUIREMENTS")
    lc.state.gates[key] = {"type": "auto"}
    assert lc.gate_requires_approval("REQUIREMENTS") is False


# ── Artifacts ─────────────────────────────────────────────────────────────────


def test_artifacts_complete_returns_false_when_missing(proj):
    lc = ProjectLifecycle.load(proj)
    lc.advance("DISCUSSING")
    lc.advance("REQUIREMENTS")
    # REQUIREMENTS phase requires REQUIREMENTS.md
    assert lc.artifacts_complete() is False


def test_artifacts_complete_returns_true_when_present(proj):
    (proj / "REQUIREMENTS.md").write_text("# Req")
    state_file = proj / ".orchid" / "project.state.json"
    state_file.write_text(json.dumps({"phase": "REQUIREMENTS"}))
    lc = ProjectLifecycle.load(proj)
    assert lc.artifacts_complete() is True


def test_can_advance_returns_true(proj):
    lc = ProjectLifecycle.load(proj)
    assert lc.can_advance() is True


def test_valid_next_phases(proj):
    lc = ProjectLifecycle.load(proj)
    nexts = lc.valid_next_phases()
    assert "DISCUSSING" in nexts
