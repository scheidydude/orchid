"""Session checkpoint restore — rewind and resume from saved snapshots."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from orchid.checkpoint.schema import Checkpoint, CheckpointEntry
from orchid.checkpoint.store import CheckpointStore

logger = logging.getLogger(__name__)


def rewind_session(
    project_dir: str | Path,
    checkpoint_id: str,
) -> Checkpoint | None:
    """
    Restore session state from a checkpoint snapshot.

    Loads the checkpoint, then overwrites the session's in-memory state
    (tasks, hot_memory, decisions, delegations, extra_context) with the
    checkpoint contents.  The session is *not* persisted to disk by this
    function — the caller should call ``session.save()`` after rewinding.

    Returns the restored ``Checkpoint`` object, or ``None`` if the
    checkpoint was not found.
    """
    store = CheckpointStore(project_dir)
    checkpoint = store.load(checkpoint_id)
    if checkpoint is None:
        logger.warning("Checkpoint %s not found in %s", checkpoint_id, project_dir)
        return None

    # Import here to avoid circular imports
    from orchid.memory.state import TaskStatus
    from orchid.session import Session

    session = Session(project_dir=project_dir)
    session.load()

    # Restore tasks — keep only tasks that existed at checkpoint time
    checkpoint_task_ids = {t.get("id", "") for t in checkpoint.tasks}
    session.tasks = [
        t for t in session.tasks
        if t.id in checkpoint_task_ids
    ]
    # Re-assign task statuses from checkpoint
    task_status_map: dict[str, TaskStatus] = {}
    for t in checkpoint.tasks:
        status_str = t.get("status", "TODO")
        try:
            task_status_map[t["id"]] = TaskStatus(status_str)
        except ValueError:
            task_status_map[t["id"]] = TaskStatus.TODO
    for t in session.tasks:
        if t.id in task_status_map:
            t.status = task_status_map[t.id]

    # Restore hot memory
    session.hot_memory = checkpoint.hot_memory

    # Restore decisions
    session.decisions = checkpoint.decisions

    # Restore delegations
    session.delegations = checkpoint.delegations

    # Restore extra context
    session.extra_context = checkpoint.extra_context

    # Restore cache stats
    session.cache_stats = checkpoint.cache_stats

    logger.info(
        "Session rewound to checkpoint %s (task=%s, %d tasks)",
        checkpoint_id,
        checkpoint.metadata.task_id,
        len(session.tasks),
    )
    return checkpoint


def list_checkpoints(
    project_dir: str | Path,
) -> list[CheckpointEntry]:
    """
    Return all checkpoint entries for a project, newest first.

    Convenience wrapper around ``CheckpointStore.list()``.
    """
    store = CheckpointStore(project_dir)
    return store.list()