"""Firecracker VM-level checkpoint metadata store (P08 Phase 6).

Distinct from orchid/checkpoint/*.py -- that package is pure JSON of
conversation/task-list *state* (ReAct message history, session task
list), with zero process/VM awareness. This stores the *paths* a
killed-and-restorable microVM needs: the snapshot files, and the same
rootfs/vsock paths Firecracker's own snapshot-load requires (its device
state hardcodes absolute paths recorded at snapshot-creation time --
see P08's ADR on path matching in the portfolio repo). One active
checkpoint per task_id.

Checkpoints reference files inside the task's original work_dir, which
is deliberately *not* cleaned up when a checkpoint is created (see
FirecrackerRunner.run_task_isolated()) -- moving the files to a new
location would require rewriting every path baked into the snapshot's
own device state, which is more failure-prone than just leaving them
where they are.

Lifetime: checkpoints don't survive a host reboot -- they reference
files under /tmp, the same as the rest of FirecrackerRunner's per-task
work dirs. Documented, not hidden.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_STORE_ROOT = Path.home() / ".orchid" / "firecracker_checkpoints"


@dataclasses.dataclass
class FirecrackerCheckpoint:
    task_id: str
    work_dir: str
    snapshot_path: str
    mem_file_path: str
    rootfs_path: str
    vsock_uds_path: str
    created_at: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> FirecrackerCheckpoint:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class FirecrackerCheckpointStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _DEFAULT_STORE_ROOT
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, task_id: str) -> Path:
        return self._root / f"{task_id}.json"

    def save(self, checkpoint: FirecrackerCheckpoint) -> None:
        self._path_for(checkpoint.task_id).write_text(json.dumps(checkpoint.to_dict()))

    def load_for_task(self, task_id: str) -> FirecrackerCheckpoint | None:
        p = self._path_for(task_id)
        if not p.exists():
            return None
        try:
            return FirecrackerCheckpoint.from_dict(json.loads(p.read_text()))
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.warning("corrupt Firecracker checkpoint for task %s: %s", task_id, e)
            return None

    def delete(self, task_id: str) -> None:
        self._path_for(task_id).unlink(missing_ok=True)

    def has_checkpoint(self, task_id: str) -> bool:
        return self._path_for(task_id).exists()
