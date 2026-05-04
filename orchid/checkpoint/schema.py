"""Session checkpoint schema — dataclasses for checkpoint state."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class CheckpointMetadata:
    """File-level metadata for a checkpoint snapshot."""

    checkpoint_id: str
    project_dir: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    task_id: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "project_dir": self.project_dir,
            "created_at": self.created_at,
            "task_id": self.task_id,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckpointMetadata:
        return cls(
            checkpoint_id=data["checkpoint_id"],
            project_dir=data["project_dir"],
            created_at=data.get("created_at", ""),
            task_id=data.get("task_id", ""),
            description=data.get("description", ""),
        )


@dataclass
class Checkpoint:
    """A snapshot of session state at a point in time."""

    metadata: CheckpointMetadata
    tasks: list[dict[str, Any]] = field(default_factory=list)
    hot_memory: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)
    delegations: list[dict[str, Any]] = field(default_factory=list)
    extra_context: str = ""
    cache_stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "tasks": self.tasks,
            "hot_memory": self.hot_memory,
            "decisions": self.decisions,
            "delegations": self.delegations,
            "extra_context": self.extra_context,
            "cache_stats": self.cache_stats,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        return cls(
            metadata=CheckpointMetadata.from_dict(data["metadata"]),
            tasks=data.get("tasks", []),
            hot_memory=data.get("hot_memory", ""),
            decisions=data.get("decisions", []),
            delegations=data.get("delegations", []),
            extra_context=data.get("extra_context", ""),
            cache_stats=data.get("cache_stats", {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> Checkpoint:
        return cls.from_dict(json.loads(text))


@dataclass
class CheckpointEntry:
    """An index entry in the checkpoint store."""

    checkpoint_id: str
    file_path: str
    created_at: str
    task_id: str = ""
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "file_path": self.file_path,
            "created_at": self.created_at,
            "task_id": self.task_id,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckpointEntry:
        return cls(
            checkpoint_id=data["checkpoint_id"],
            file_path=data["file_path"],
            created_at=data["created_at"],
            task_id=data.get("task_id", ""),
            size_bytes=data.get("size_bytes", 0),
        )
