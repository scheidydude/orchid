"""Session checkpoint store — persist and retrieve checkpoint snapshots."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchid.checkpoint.schema import Checkpoint, CheckpointEntry, CheckpointMetadata, ReActCheckpoint

logger = logging.getLogger(__name__)


class CheckpointStore:
    """
    File-based checkpoint store for Orchid session snapshots.

    Layout::

        <project_dir>/.orchid/checkpoints/
        ├── <checkpoint_id>.json        # full checkpoint snapshot
        └── index.json                  # index of all checkpoints

    Each checkpoint file stores a single ``Checkpoint`` (JSON-serialised).
    The index tracks metadata for every stored checkpoint so listing and
    pruning are O(1) without reading every file.
    """

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._dir = self.project_dir / ".orchid" / "checkpoints"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"
        self._index: list[CheckpointEntry] = self._load_index()

    # ── Public API ──────────────────────────────────────────────────────────────

    def save(
        self,
        tasks: list[dict[str, Any]],
        hot_memory: str = "",
        decisions: list[dict[str, Any]] | None = None,
        delegations: list[dict[str, Any]] | None = None,
        extra_context: str = "",
        cache_stats: dict[str, int] | None = None,
        task_id: str = "",
        description: str = "",
    ) -> Checkpoint:
        """
        Capture a checkpoint snapshot and persist it to disk.

        Returns the saved ``Checkpoint`` object.
        """
        checkpoint_id = uuid.uuid4().hex[:12]
        metadata = CheckpointMetadata(
            checkpoint_id=checkpoint_id,
            project_dir=str(self.project_dir),
            task_id=task_id,
            description=description,
        )
        checkpoint = Checkpoint(
            metadata=metadata,
            tasks=tasks,
            hot_memory=hot_memory,
            decisions=decisions or [],
            delegations=delegations or [],
            extra_context=extra_context,
            cache_stats=cache_stats or {},
        )

        file_path = self._dir / f"{checkpoint_id}.json"
        file_path.write_text(checkpoint.to_json(), encoding="utf-8")

        size_bytes = file_path.stat().st_size
        entry = CheckpointEntry(
            checkpoint_id=checkpoint_id,
            file_path=str(file_path),
            created_at=metadata.created_at,
            task_id=task_id,
            size_bytes=size_bytes,
        )
        self._index.append(entry)
        self._save_index()

        logger.info(
            "Checkpoint saved: %s (task=%s, %d bytes)",
            checkpoint_id, task_id, size_bytes,
        )
        return checkpoint

    def load(self, checkpoint_id: str) -> Checkpoint | None:
        """
        Load a checkpoint by ID.

        Returns ``None`` if the checkpoint is not found.
        """
        entry = self._find_entry(checkpoint_id)
        if entry is None:
            return None
        file_path = Path(entry.file_path)
        if not file_path.exists():
            logger.warning("Checkpoint file missing: %s", entry.file_path)
            self._remove_entry(checkpoint_id)
            return None
        try:
            text = file_path.read_text(encoding="utf-8")
            return Checkpoint.from_json(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load checkpoint %s: %s", checkpoint_id, exc)
            return None

    def list(self) -> list[CheckpointEntry]:
        """Return all checkpoint entries, newest first."""
        return sorted(self._index, key=lambda e: e.created_at, reverse=True)

    def delete(self, checkpoint_id: str) -> bool:
        """
        Delete a checkpoint by ID.

        Returns ``True`` if the checkpoint was found and removed.
        """
        entry = self._find_entry(checkpoint_id)
        if entry is None:
            return False
        file_path = Path(entry.file_path)
        if file_path.exists():
            file_path.unlink()
        self._remove_entry(checkpoint_id)
        logger.info("Checkpoint deleted: %s", checkpoint_id)
        return True

    def prune(self, keep: int = 5) -> int:
        """
        Remove old checkpoints, keeping only the most recent *keep*.

        Returns the number of checkpoints removed.
        """
        entries = self.list()  # newest first
        to_remove = entries[keep:]
        for entry in to_remove:
            file_path = Path(entry.file_path)
            if file_path.exists():
                file_path.unlink()
            self._remove_entry(entry.checkpoint_id)
        removed = len(to_remove)
        if removed:
            logger.info("Pruned %d old checkpoint(s), kept %d", removed, keep)
        return removed

    # ── ReAct checkpoint methods ────────────────────────────────────────────────

    def save_react_checkpoint(self, cp: ReActCheckpoint) -> Path:
        """Save a mid-task ReAct checkpoint. Overwrites previous checkpoint for same task_id."""
        cp.timestamp = datetime.now(UTC).isoformat()
        dest = self._dir / f"react_{cp.task_id}.json"
        dest.write_text(json.dumps(asdict(cp)))
        return dest

    def load_react_checkpoint(self, task_id: str) -> ReActCheckpoint | None:
        """Load a mid-task ReAct checkpoint for task_id, or None if not found."""
        path = self._dir / f"react_{task_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ReActCheckpoint(**data)

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _load_index(self) -> list[CheckpointEntry]:
        if not self._index_path.exists():
            return []
        try:
            text = self._index_path.read_text(encoding="utf-8")
            data = json.loads(text)
            return [CheckpointEntry.from_dict(d) for d in data]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load checkpoint index: %s", exc)
            return []

    def _save_index(self) -> None:
        data = [e.to_dict() for e in self._index]
        self._index_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _find_entry(self, checkpoint_id: str) -> CheckpointEntry | None:
        for entry in self._index:
            if entry.checkpoint_id == checkpoint_id:
                return entry
        return None

    def _remove_entry(self, checkpoint_id: str) -> None:
        self._index = [e for e in self._index if e.checkpoint_id != checkpoint_id]
        self._save_index()