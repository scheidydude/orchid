"""Task injection tools — programmatically add tasks to a session."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchid.session import Session

from orchid import config as cfg
from orchid.memory.state import Task, TaskStatus, load_tasks, save_tasks
from orchid.scheduler import DependencyGraph, CyclicDependencyError

logger = logging.getLogger(__name__)

# Thread-local storage so each parallel worker thread has its own session ref.
_local = threading.local()


def set_active_session(session: "Session") -> None:
    """Wire the current session into this thread's task injection context."""
    _local.session = session


def spawn_task(
    title: str,
    agent_type: str = "developer",
    depends_on: str = "",
) -> str:
    """Add a new task to the run queue at runtime.

    Args:
        title: Task description (becomes the task title in tasks.md).
        agent_type: Agent type: developer, tester, researcher, reviewer.
        depends_on: Comma-separated task IDs this task depends on (e.g. "T010,T011").
                    Empty string means no dependencies.

    Returns:
        The new task ID (e.g. "T042") or an error string starting with "[error".
    """
    session = getattr(_local, "session", None)
    if session is None:
        return "[error: no active session — spawn_task only works inside an agent run]"
    dep_list = [d.strip() for d in depends_on.split(",") if d.strip()]
    try:
        new_task = session.inject_task(
            title=title,
            agent=agent_type,
            depends_on=dep_list,
        )
        return f"Task {new_task.id} created: {title!r}"
    except Exception as e:
        logger.error("spawn_task failed: %s", e)
        return f"[error: {e}]"


def inject_task(
    project_dir: str | Path,
    title: str,
    task_type: str = "draft",
    priority: int = 2,
    description: str = "",
    depends_on: list[str] | None = None,
    agent: str | None = None,
    model_override: str | None = None,
    tags: list[str] | None = None,
    rollup_sources: list[str] | None = None,
    output_file: str | None = None,
) -> Task:
    """Add a new task to the project's tasks.md and return the Task object.

    The task is appended with status TODO, given a unique T#### ID,
    and persisted to disk immediately so the scheduler picks it up
    on the next cycle.

    Args:
        project_dir: Path to the external project directory.
        title: Human-readable task title.
        task_type: Task type — determines model routing
                   (e.g. "code_generate", "review", "search", "verify",
                    "rollup", "draft").
        priority: 1=high, 2=normal, 3=low.
        description: Detailed description / acceptance criteria.
        depends_on: List of task IDs this task depends on (must be DONE).
        agent: Optional agent class override (e.g. "developer", "reviewer").
        model_override: Optional model override (e.g. "claude", "local").
        tags: Optional list of tags for the task.
        rollup_sources: For rollup-type tasks, list of source task IDs.
        output_file: For rollup-type tasks, the output filename.

    Returns:
        The newly created Task object.

    Raises:
        ValueError: If title is empty or depends_on references non-existent tasks.
    """
    project_dir = Path(project_dir).resolve()
    tasks = load_tasks(project_dir)
    task_ids = {t.id for t in tasks}

    if not title.strip():
        raise ValueError("inject_task: title must not be empty")

    # Validate depends_on references
    if depends_on:
        missing = set(depends_on) - task_ids
        if missing:
            raise ValueError(
                f"inject_task: depends_on references unknown task IDs: {missing}"
            )

    # Validate rollup_sources references
    if rollup_sources:
        missing = set(rollup_sources) - task_ids
        if missing:
            raise ValueError(
                f"inject_task: rollup_sources references unknown task IDs: {missing}"
            )

    # Generate unique task ID
    max_num = 0
    for t in tasks:
        try:
            num = int(t.id.lstrip("T"))
            if num > max_num:
                max_num = num
        except ValueError:
            pass
    new_id = f"T{max_num + 1:03d}"

    task = Task(
        id=new_id,
        title=title.strip(),
        status=TaskStatus.TODO,
        type=task_type,
        priority=priority,
        description=description.strip(),
        agent=agent,
        tags=tags or [],
        depends_on=depends_on or [],
        model_override=model_override,
        rollup_sources=rollup_sources or [],
        output_file=output_file,
    )
    tasks.append(task)
    save_tasks(tasks, project_dir)

    # T221: Detect dependency cycles introduced by this injection
    _graph = DependencyGraph(tasks)
    if _graph.has_cycle():
        # Roll back the injected task
        tasks = [t for t in tasks if t.id != new_id]
        save_tasks(tasks, project_dir)
        raise CyclicDependencyError(
            f"inject_task('{title}') would create a dependency cycle — task not added"
        )

    logger.info(
        "Task injected: %s — %s (type=%s, priority=%d, depends_on=%s)",
        new_id, title, task_type, priority, task.depends_on,
    )

    return task


def remove_task(
    project_dir: str | Path,
    task_id: str,
    status: TaskStatus = TaskStatus.CANCELLED,
) -> bool:
    """Mark a task as cancelled (or another terminal status) in tasks.md.

    Args:
        project_dir: Path to the project directory.
        task_id: The task ID to remove (e.g. "T001").
        status: New status — defaults to CANCELLED.

    Returns:
        True if the task was found and updated, False otherwise.
    """
    project_dir = Path(project_dir).resolve()
    tasks = load_tasks(project_dir)

    for task in tasks:
        if task.id == task_id:
            task.status = status
            save_tasks(tasks, project_dir)
            logger.info("Task %s marked as %s.", task_id, status.value)
            return True

    logger.warning("Task %s not found — nothing to remove.", task_id)
    return False


def get_task(
    project_dir: str | Path,
    task_id: str,
) -> Task | None:
    """Return the Task object for task_id, or None if not found."""
    project_dir = Path(project_dir).resolve()
    tasks = load_tasks(project_dir)

    for task in tasks:
        if task.id == task_id:
            return task
    return None


def list_tasks(
    project_dir: str | Path,
    status_filter: TaskStatus | None = None,
) -> list[Task]:
    """Return tasks from tasks.md, optionally filtered by status."""
    project_dir = Path(project_dir).resolve()
    tasks = load_tasks(project_dir)

    if status_filter:
        return [t for t in tasks if t.status == status_filter]
    return tasks


def task_exists(
    project_dir: str | Path,
    task_id: str,
) -> bool:
    """Return True if task_id exists in the project's tasks.md."""
    return get_task(project_dir, task_id) is not None