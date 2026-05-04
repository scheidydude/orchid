"""Tests for CheckpointStore — save, load, list, delete, prune."""

import json
from pathlib import Path

import pytest

from orchid.checkpoint.store import CheckpointStore


@pytest.fixture
def store(tmp_path: Path) -> CheckpointStore:
    """Return a CheckpointStore backed by a temporary directory."""
    return CheckpointStore(tmp_path)


def test_save_and_load(store: CheckpointStore) -> None:
    """Saving a checkpoint and loading it back returns identical data."""
    tasks = [{"id": "T001", "title": "Build login", "status": "TODO"}]
    checkpoint = store.save(
        tasks=tasks,
        hot_memory="user said 'make it fast'",
        task_id="T001",
        description="Before login implementation",
    )

    loaded = store.load(checkpoint.metadata.checkpoint_id)
    assert loaded is not None
    assert loaded.metadata.checkpoint_id == checkpoint.metadata.checkpoint_id
    assert loaded.tasks == tasks
    assert loaded.hot_memory == "user said 'make it fast'"
    assert loaded.metadata.task_id == "T001"
    assert loaded.metadata.description == "Before login implementation"


def test_load_nonexistent(store: CheckpointStore) -> None:
    """Loading a checkpoint ID that was never saved returns None."""
    result = store.load("nonexistent_id_123456")
    assert result is None


def test_list_returns_newest_first(store: CheckpointStore) -> None:
    """list() returns entries sorted newest-first by created_at."""
    cp1 = store.save(tasks=[], task_id="T001")
    cp2 = store.save(tasks=[], task_id="T002")
    cp3 = store.save(tasks=[], task_id="T003")

    entries = store.list()
    assert len(entries) == 3
    # Newest first means cp3 (last saved) appears first
    assert entries[0].checkpoint_id == cp3.metadata.checkpoint_id
    assert entries[1].checkpoint_id == cp2.metadata.checkpoint_id
    assert entries[2].checkpoint_id == cp1.metadata.checkpoint_id


def test_delete_removes_file_and_index(store: CheckpointStore) -> None:
    """Deleting a checkpoint removes its file and index entry."""
    checkpoint = store.save(tasks=[], task_id="T001")
    assert store.delete(checkpoint.metadata.checkpoint_id) is True

    # File should be gone
    assert not Path(checkpoint.metadata.checkpoint_id + ".json").exists()
    # Index entry should be gone
    assert store.load(checkpoint.metadata.checkpoint_id) is None
    # Listing should be empty
    assert store.list() == []


def test_delete_nonexistent(store: CheckpointStore) -> None:
    """Deleting a non-existent checkpoint returns False."""
    assert store.delete("no_such_id") is False


def test_prune_keeps_recent(store: CheckpointStore) -> None:
    """prune(keep=2) removes all but the two most recent checkpoints."""
    cp1 = store.save(tasks=[], task_id="T001")
    cp2 = store.save(tasks=[], task_id="T002")
    cp3 = store.save(tasks=[], task_id="T003")
    cp4 = store.save(tasks=[], task_id="T004")

    removed = store.prune(keep=2)
    assert removed == 2  # cp1 and cp2 should be removed

    # Only cp3 and cp4 remain
    entries = store.list()
    assert len(entries) == 2
    assert entries[0].checkpoint_id == cp4.metadata.checkpoint_id
    assert entries[1].checkpoint_id == cp3.metadata.checkpoint_id


def test_prune_noop_when_fewer_than_keep(store: CheckpointStore) -> None:
    """prune(keep=5) with only 3 checkpoints removes nothing."""
    store.save(tasks=[], task_id="T001")
    store.save(tasks=[], task_id="T002")
    store.save(tasks=[], task_id="T003")

    removed = store.prune(keep=5)
    assert removed == 0
    assert len(store.list()) == 3


def test_save_persists_index_json(store: CheckpointStore) -> None:
    """After saving, index.json exists and is valid JSON with the entry."""
    store.save(tasks=[], task_id="T001")

    index_path = store._dir / "index.json"
    assert index_path.exists()
    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["task_id"] == "T001"