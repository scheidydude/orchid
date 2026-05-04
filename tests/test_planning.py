"""Tests for orchid/planning.py — PlanningSession."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def proj(tmp_path):
    """Return a temp project dir with .orchid/ created."""
    (tmp_path / ".orchid").mkdir()
    return tmp_path


@pytest.fixture
def planning_session(proj):
    """Return a PlanningSession with mocked provider."""
    from orchid.planning import PlanningSession

    with patch("orchid.planning._get_provider_registry") as mock_reg:
        mock_provider = MagicMock()
        mock_provider.complete.return_value = "Response"
        mock_reg.return_value.resolve.return_value = mock_provider
        session = PlanningSession(str(proj))
        session.provider = mock_provider
        return session


# ── Initialization ────────────────────────────────────────────────────────────


def test_planning_session_creates_history_file(proj):
    from orchid.planning import PlanningSession

    session = PlanningSession(str(proj))
    assert session.history_file.parent.exists()
    assert session.conversation == []


def test_planning_session_loads_existing_history(proj):
    from orchid.planning import PlanningSession

    history = {"conversation": [{"role": "user", "content": "Hello"}]}
    (proj / ".orchid" / "planning_history.json").write_text(json.dumps(history))

    session = PlanningSession(str(proj))
    assert len(session.conversation) == 1
    assert session.conversation[0]["content"] == "Hello"


def test_planning_session_handles_corrupt_history(proj):
    from orchid.planning import PlanningSession

    (proj / ".orchid" / "planning_history.json").write_text("not valid json")

    session = PlanningSession(str(proj))
    assert session.conversation == []


# ── History methods ───────────────────────────────────────────────────────────


def test_get_history_returns_formatted_list(planning_session):
    planning_session.conversation = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]

    history = planning_session.get_history()
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Hello"}
    assert history[1] == {"role": "assistant", "content": "Hi there"}


# ── Chat functionality ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_appends_user_message(planning_session):
    await planning_session.chat("Hello")

    assert len(planning_session.conversation) == 2
    assert planning_session.conversation[0]["role"] == "user"
    assert planning_session.conversation[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_chat_calls_provider_complete(planning_session):
    await planning_session.chat("Hello")

    planning_session.provider.complete.assert_called_once()
    call_args = planning_session.provider.complete.call_args
    assert any(msg["content"] == "Hello" for msg in call_args.kwargs["messages"])


@pytest.mark.asyncio
async def test_chat_saves_history(planning_session, proj):
    await planning_session.chat("Hello")

    assert planning_session.history_file.exists()
    data = json.loads(planning_session.history_file.read_text())
    assert len(data["conversation"]) == 2


@pytest.mark.asyncio
async def test_chat_returns_provider_response(planning_session):
    planning_session.provider.complete.return_value = "AI response"

    response = await planning_session.chat("Hello")

    assert response == "AI response"


# ── Artifact saving ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_saves_artifacts_when_present(planning_session, proj):
    response = """
    <artifact name="REQUIREMENTS.md">
    # Requirements
    - Feature 1
    </artifact>
    <artifact name="ARCHITECTURE.md">
    # Architecture
    System design
    </artifact>
    """
    planning_session.provider.complete.return_value = response

    await planning_session.chat("Generate artifacts")

    assert (proj / "REQUIREMENTS.md").exists()
    assert (proj / "ARCHITECTURE.md").exists()
    assert "# Requirements" in (proj / "REQUIREMENTS.md").read_text()


@pytest.mark.asyncio
async def test_chat_calls_status_callback_for_artifacts(planning_session, proj):
    response = """
    <artifact name="tasks.md">
    - [ ] **T001** Task
    </artifact>
    """
    planning_session.provider.complete.return_value = response

    callback = AsyncMock()
    await planning_session.chat("Generate", status_callback=callback)

    assert callback.called
    calls = [str(c) for c in callback.call_args_list]
    assert any("Generating tasks.md" in c for c in calls)
    assert any("artifacts_ready:tasks.md" in c for c in calls)


@pytest.mark.asyncio
async def test_chat_does_not_save_without_artifacts(planning_session, proj):
    planning_session.provider.complete.return_value = "Just a normal response"

    await planning_session.chat("Hello")

    assert not (proj / "REQUIREMENTS.md").exists()
    assert not (proj / "ARCHITECTURE.md").exists()


# ── Artifact parsing edge cases ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_artifact_with_multiline_content(planning_session, proj):
    response = """
    <artifact name="REQUIREMENTS.md">
    Line 1
    Line 2
    Line 3
    </artifact>
    """
    planning_session.provider.complete.return_value = response

    await planning_session.chat("Generate")

    content = (proj / "REQUIREMENTS.md").read_text()
    assert "Line 1" in content
    assert "Line 2" in content
    assert "Line 3" in content


@pytest.mark.asyncio
async def test_artifact_with_whitespace_trimmed(planning_session, proj):
    response = """
    <artifact name="tasks.md">
    
    - [ ] **T001** Task
    
    </artifact>
    """
    planning_session.provider.complete.return_value = response

    await planning_session.chat("Generate")

    content = (proj / "tasks.md").read_text()
    assert content.strip().startswith("- [ ] **T001**")


@pytest.mark.asyncio
async def test_multiple_artifacts_same_name_last_wins(planning_session, proj):
    response = """
    <artifact name="REQUIREMENTS.md">First</artifact>
    <artifact name="REQUIREMENTS.md">Second</artifact>
    """
    planning_session.provider.complete.return_value = response

    await planning_session.chat("Generate")

    content = (proj / "REQUIREMENTS.md").read_text()
    assert "Second" in content


# ── Status callback edge cases ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_callback_none_no_error(planning_session, proj):
    response = """
    <artifact name="REQUIREMENTS.md">Content</artifact>
    """
    planning_session.provider.complete.return_value = response

    # No status_callback passed
    await planning_session.chat("Generate")

    assert (proj / "REQUIREMENTS.md").exists()


@pytest.mark.asyncio
async def test_status_callback_async(planning_session, proj):
    response = """
    <artifact name="REQUIREMENTS.md">Content</artifact>
    """
    planning_session.provider.complete.return_value = response

    callback = AsyncMock()
    await planning_session.chat("Generate", status_callback=callback)

    callback.assert_called()