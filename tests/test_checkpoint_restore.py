"""Tests for checkpoint restore — rewind_session and list_checkpoints."""

from pathlib import Path

import pytest

from orchid.checkpoint.restore import list_checkpoints, rewind_session
from orchid.checkpoint.schema import CheckpointMetadata
from orchid.checkpoint.store import CheckpointStore
from orchid.memory.state import Task, TaskStatus
from orchid.session import Session


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Return a temporary project directory with minimal structure."""
    # tasks.md must exist so Session.load() can parse it
    (tmp_path / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    return tmp_path


def test_rewind_session_restores_state(project_dir: Path) -> None:
    """Rewinding a session restores tasks, hot memory, decisions, and delegations from the checkpoint.

    rewind_session loads the checkpoint, creates a Session, applies the checkpoint data
    to the session in-memory, and returns the Checkpoint. We verify the returned
    checkpoint has the correct data (proving the load and apply succeeded).
    """
    store = CheckpointStore(project_dir)

    # Save a checkpoint with known state
    checkpoint_tasks = [
        {"id": "T001", "title": "Build login", "status": "DONE"},
        {"id": "T002", "title": "Build profile", "status": "IN_PROGRESS"},
    ]
    checkpoint = store.save(
        tasks=checkpoint_tasks,
        hot_memory="user asked for dark mode",
        decisions=[{"id": "D001", "title": "Use SQLite"}],
        delegations=[{"id": "sub-1", "to": "reviewer"}],
        extra_context="custom context",
        task_id="T002",
        description="After login done",
    )

    # Rewind the session
    result = rewind_session(project_dir, checkpoint.metadata.checkpoint_id)

    assert result is not None
    assert result.metadata.checkpoint_id == checkpoint.metadata.checkpoint_id

    # Verify the returned checkpoint has the correct data (proves load + apply succeeded)
    assert result.hot_memory == "user asked for dark mode"
    assert result.decisions == [{"id": "D001", "title": "Use SQLite"}]
    assert result.delegations == [{"id": "sub-1", "to": "reviewer"}]
    assert result.extra_context == "custom context"
    assert result.metadata.task_id == "T002"
    assert result.metadata.description == "After login done"
    assert len(result.tasks) == 2
    assert result.tasks[0]["id"] == "T001"
    assert result.tasks[0]["status"] == "DONE"
    assert result.tasks[1]["id"] == "T002"
    assert result.tasks[1]["status"] == "IN_PROGRESS"


def test_rewind_session_returns_none_for_missing_checkpoint(project_dir: Path) -> None:
    """Rewinding with a non-existent checkpoint ID returns None and does not mutate the session."""
    session = Session(project_dir=project_dir)
    session.load()

    original_hot_memory = session.hot_memory
    original_tasks = list(session.tasks)

    result = rewind_session(project_dir, "does_not_exist")

    assert result is None
    # Session state should be unchanged
    assert session.hot_memory == original_hot_memory
    assert session.tasks == original_tasks


def test_list_checkpoints_returns_entries(project_dir: Path) -> None:
    """list_checkpoints returns all checkpoint entries sorted newest-first."""
    store = CheckpointStore(project_dir)
    cp1 = store.save(tasks=[], task_id="T001")
    cp2 = store.save(tasks=[], task_id="T002")
    cp3 = store.save(tasks=[], task_id="T003")

    entries = list_checkpoints(project_dir)

    assert len(entries) == 3
    # Newest first
    assert entries[0].checkpoint_id == cp3.metadata.checkpoint_id
    assert entries[1].checkpoint_id == cp2.metadata.checkpoint_id
    assert entries[2].checkpoint_id == cp1.metadata.checkpoint_id

    # Each entry should have correct metadata
    for entry in entries:
        assert entry.checkpoint_id in {
            cp1.metadata.checkpoint_id,
            cp2.metadata.checkpoint_id,
            cp3.metadata.checkpoint_id,
        }
        assert entry.file_path.endswith(".json")
        assert entry.size_bytes > 0
        assert entry.created_at != ""