"""Tests for export_checkpoint in orchid.checkpoint.restore."""

import json
from pathlib import Path

import pytest

from orchid.checkpoint.restore import export_checkpoint
from orchid.checkpoint.store import CheckpointStore


def test_export_checkpoint_writes_file(tmp_path: Path) -> None:
    """Export a checkpoint and verify the exported JSON contains the correct metadata."""
    store = CheckpointStore(tmp_path)
    checkpoint = store.save(
        tasks=[],
        hot_memory="",
        decisions=[],
        delegations=[],
        task_id="T001",
        description="Test export",
    )
    checkpoint_id = checkpoint.metadata.checkpoint_id

    export_dir = tmp_path / "export"
    exported_path = export_checkpoint(checkpoint_id, tmp_path, export_dir)

    assert exported_path.exists()
    data = json.loads(exported_path.read_text())
    assert data["metadata"]["task_id"] == "T001"
    assert data["metadata"]["checkpoint_id"] == checkpoint_id
    assert data["tasks"] == []
    assert data["hot_memory"] == ""
    assert data["decisions"] == []
    assert data["delegations"] == []


def test_export_checkpoint_raises_for_missing(tmp_path: Path) -> None:
    """Export a non-existent checkpoint should raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Checkpoint 'NOTEXIST' not found"):
        export_checkpoint("NOTEXIST", tmp_path, tmp_path / "export")