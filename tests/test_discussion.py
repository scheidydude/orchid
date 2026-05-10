"""Tests for orchid/discussion.py — DiscussionHistory."""

import json

import pytest

from orchid.discussion import DiscussionHistory

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".orchid").mkdir()
    return tmp_path


# ── Append / load ─────────────────────────────────────────────────────────────


def test_history_appends_and_loads(proj):
    h = DiscussionHistory.load(proj)
    h.append("user", "I want a recipe app")
    h.append("agent", "What features do you need?")

    h2 = DiscussionHistory.load(proj)
    entries = h2.get_full_history()
    assert len(entries) == 2
    assert entries[0]["role"] == "user"
    assert entries[1]["role"] == "agent"


def test_turn_count(proj):
    h = DiscussionHistory.load(proj)
    assert h.turn_count() == 0
    h.append("user", "Hello")
    h.append("agent", "Hi!")
    assert h.turn_count() == 2


def test_turn_count_persists_across_loads(proj):
    h = DiscussionHistory.load(proj)
    for i in range(5):
        h.append("user", f"message {i}")
    h2 = DiscussionHistory.load(proj)
    assert h2.turn_count() == 5


def test_turn_numbers_are_sequential(proj):
    h = DiscussionHistory.load(proj)
    h.append("user", "a")
    h.append("user", "b")
    h.append("user", "c")
    turns = [e["turn"] for e in h.get_full_history()]
    assert turns == [1, 2, 3]


# ── Recent ────────────────────────────────────────────────────────────────────


def test_recent_returns_n_turns(proj):
    h = DiscussionHistory.load(proj)
    for i in range(15):
        h.append("user", f"msg {i}")
    recent = h.get_recent(5)
    assert len(recent) == 5
    assert recent[0]["message"] == "msg 10"
    assert recent[-1]["message"] == "msg 14"


def test_recent_returns_all_when_less_than_n(proj):
    h = DiscussionHistory.load(proj)
    h.append("user", "only one")
    assert len(h.get_recent(10)) == 1


# ── Context MD ────────────────────────────────────────────────────────────────


def test_context_md_returns_default_when_missing(proj):
    h = DiscussionHistory.load(proj)
    ctx = h.get_context_md()
    assert "## Project Intent" in ctx


def test_context_md_updated(proj):
    h = DiscussionHistory.load(proj)
    h.update_context("- Backend: FastAPI (confirmed)")
    ctx = h.get_context_md()
    assert "FastAPI" in ctx


def test_context_md_persists(proj):
    h = DiscussionHistory.load(proj)
    h.update_context("- Auth: JWT tokens")
    h2 = DiscussionHistory.load(proj)
    assert "JWT" in h2.get_context_md()


def test_update_context_empty_string_is_noop(proj):
    h = DiscussionHistory.load(proj)
    h.update_context("")
    # context.md should not be created yet
    assert not (proj / ".orchid" / "discussion" / "context.md").exists()


# ── Prompt context ────────────────────────────────────────────────────────────


def test_to_prompt_context_format(proj):
    h = DiscussionHistory.load(proj)
    h.append("user", "I want X")
    h.append("agent", "Tell me more")
    ctx = h.to_prompt_context()
    assert "## Discussion History" in ctx
    assert "User (turn 1)" in ctx
    assert "Agent (turn 2)" in ctx
    assert "I want X" in ctx


def test_to_prompt_context_empty_when_no_entries(proj):
    h = DiscussionHistory.load(proj)
    assert h.to_prompt_context() == ""


# ── Phase tracking ────────────────────────────────────────────────────────────


def test_append_records_phase(proj):
    h = DiscussionHistory.load(proj)
    h.append("user", "hello", phase="PLANNING")
    data = json.loads((proj / ".orchid" / "discussion" / "conversation.jsonl").read_text())
    assert data["phase"] == "PLANNING"


# ── Dir auto-creation ─────────────────────────────────────────────────────────


def test_discussion_dir_created_on_append(tmp_path):
    h = DiscussionHistory.load(tmp_path)
    # conversation.jsonl doesn't exist until first append
    assert not (tmp_path / ".orchid" / "discussion" / "conversation.jsonl").exists()
    h.append("user", "hello")
    assert (tmp_path / ".orchid" / "discussion" / "conversation.jsonl").exists()
