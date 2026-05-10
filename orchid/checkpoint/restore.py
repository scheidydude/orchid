"""Session checkpoint restore — rewind and resume from saved snapshots."""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import UTC
from pathlib import Path

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


def resume_orphaned_tasks(project_dir: str | Path) -> int:
    """Recover tasks left IN_PROGRESS by a previous crash.

    For each IN_PROGRESS task:
    - If a ReAct checkpoint exists and is < 24h old → task stays IN_PROGRESS,
      orchestrator will resume from the checkpoint on next run.
    - If no checkpoint or too old → reset to TODO so it re-runs from scratch.

    Returns the number of tasks found (not necessarily resumed — some reset to TODO).
    """
    from orchid.memory.state import TaskStatus, load_tasks, save_tasks

    project_dir = Path(project_dir)
    store = CheckpointStore(project_dir)

    try:
        tasks = load_tasks(project_dir)
    except Exception as exc:
        logger.warning("Could not load tasks for orphan recovery in %s: %s", project_dir, exc)
        return 0

    orphans = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
    if not orphans:
        return 0

    from datetime import datetime, timedelta

    max_age = timedelta(hours=24)
    now = datetime.now(UTC)
    changed = False

    for task in orphans:
        cp = store.load_react_checkpoint(task.id)
        recovered = False
        if cp is not None and cp.timestamp:
            try:
                ts = datetime.fromisoformat(cp.timestamp)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if now - ts <= max_age:
                    # Keep IN_PROGRESS — orchestrator will resume from checkpoint
                    logger.info("[recovery] task %s: will resume from iter %d", task.id, cp.iteration)
                    recovered = True
            except Exception:
                pass

        if not recovered:
            logger.info("[recovery] task %s: no valid checkpoint, resetting to TODO", task.id)
            task.status = TaskStatus.TODO
            changed = True

    if changed:
        save_tasks(tasks, project_dir)

    return len(orphans)


def export_checkpoint(
    checkpoint_id: str,
    source_project_dir: Path,
    dest_dir: Path,
) -> Path:
    """Copy a checkpoint's files to dest_dir for transfer to a remote node.

    Returns the path to the exported checkpoint JSON in dest_dir.
    Raises FileNotFoundError if the checkpoint does not exist.
    """
    store = CheckpointStore(source_project_dir)
    cp = store.load(checkpoint_id)
    if cp is None:
        raise FileNotFoundError(f"Checkpoint {checkpoint_id!r} not found in {source_project_dir}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / f"{checkpoint_id}.json"
    # Re-serialize the checkpoint to the destination
    dest_file.write_text(json.dumps(dataclasses.asdict(cp)))
    return dest_file