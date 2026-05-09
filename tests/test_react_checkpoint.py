"""Tests for ReAct checkpoint save/load in CheckpointStore."""

from pathlib import Path

from orchid.checkpoint.schema import ReActCheckpoint
from orchid.checkpoint.store import CheckpointStore


def test_save_react_checkpoint_writes_file(tmp_path: Path) -> None:
    """Verify that save_react_checkpoint creates the expected file on disk."""
    store = CheckpointStore(tmp_path)
    cp = ReActCheckpoint(
        task_id="T001",
        iteration=5,
        conversation_history=[{"role": "user", "content": "hi"}],
    )
    dest = store.save_react_checkpoint(cp)
    assert dest.exists(), f"Expected file {dest} was not created"


def test_load_react_checkpoint_returns_none_if_missing(tmp_path: Path) -> None:
    """Verify that load_react_checkpoint returns None when no file exists."""
    store = CheckpointStore(tmp_path)
    result = store.load_react_checkpoint("NOTEXIST")
    assert result is None


def test_save_and_load_react_checkpoint_roundtrip(tmp_path: Path) -> None:
    """Verify that saving and loading a checkpoint round-trips correctly."""
    store = CheckpointStore(tmp_path)
    cp = ReActCheckpoint(
        task_id="T001",
        iteration=5,
        conversation_history=[{"role": "user", "content": "hi"}],
    )
    store.save_react_checkpoint(cp)
    loaded = store.load_react_checkpoint("T001")
    assert loaded is not None
    assert loaded.task_id == "T001"
    assert loaded.iteration == 5
    assert loaded.conversation_history == [{"role": "user", "content": "hi"}]