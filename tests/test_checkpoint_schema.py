"""Tests for orchid.checkpoint.schema — CheckpointMetadata, Checkpoint, CheckpointEntry."""

from orchid.checkpoint.schema import Checkpoint, CheckpointEntry, CheckpointMetadata


def test_checkpoint_metadata_round_trip():
    """CheckpointMetadata serialises to dict and deserialises back with the same values."""
    meta = CheckpointMetadata(
        checkpoint_id="cp-001",
        project_dir="/tmp/proj",
        task_id="T042",
        description="Before T042",
    )
    d = meta.to_dict()
    assert d["checkpoint_id"] == "cp-001"
    assert d["project_dir"] == "/tmp/proj"
    assert d["task_id"] == "T042"
    assert d["description"] == "Before T042"
    assert "created_at" in d

    restored = CheckpointMetadata.from_dict(d)
    assert restored.checkpoint_id == meta.checkpoint_id
    assert restored.project_dir == meta.project_dir
    assert restored.task_id == meta.task_id
    assert restored.description == meta.description
    assert restored.created_at == meta.created_at


def test_checkpoint_full_round_trip_via_json():
    """Checkpoint serialises to JSON and deserialises back with all fields intact."""
    meta = CheckpointMetadata(checkpoint_id="cp-002", project_dir="/tmp/p2")
    cp = Checkpoint(
        metadata=meta,
        tasks=[{"id": "T001", "status": "DONE"}, {"id": "T002", "status": "IN_PROGRESS"}],
        hot_memory="some memory",
        decisions=[{"id": "D001", "title": "Use SQLite"}],
        delegations=[{"id": "sub-1", "to": "reviewer"}],
        extra_context="ctx",
        cache_stats={"cache_hits": 5, "cache_writes": 3},
    )

    json_str = cp.to_json()
    restored = Checkpoint.from_json(json_str)

    assert restored.metadata.checkpoint_id == "cp-002"
    assert len(restored.tasks) == 2
    assert restored.tasks[0]["id"] == "T001"
    assert restored.hot_memory == "some memory"
    assert restored.decisions == [{"id": "D001", "title": "Use SQLite"}]
    assert restored.delegations == [{"id": "sub-1", "to": "reviewer"}]
    assert restored.extra_context == "ctx"
    assert restored.cache_stats == {"cache_hits": 5, "cache_writes": 3}


def test_checkpoint_entry_round_trip():
    """CheckpointEntry serialises to dict and deserialises back correctly."""
    entry = CheckpointEntry(
        checkpoint_id="cp-003",
        file_path="/tmp/p3/.orchid/checkpoints/cp-003.json",
        created_at="2026-01-01T00:00:00+00:00",
        task_id="T010",
        size_bytes=1024,
    )
    d = entry.to_dict()
    assert d["checkpoint_id"] == "cp-003"
    assert d["size_bytes"] == 1024

    restored = CheckpointEntry.from_dict(d)
    assert restored.checkpoint_id == entry.checkpoint_id
    assert restored.file_path == entry.file_path
    assert restored.created_at == entry.created_at
    assert restored.task_id == entry.task_id
    assert restored.size_bytes == entry.size_bytes
