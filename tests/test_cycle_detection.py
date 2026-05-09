"""tests/test_cycle_detection.py — Cycle detection in DependencyGraph."""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so imports like `orchid.scheduler` work.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from orchid.scheduler import DependencyGraph, CyclicDependencyError, Scheduler
from orchid.memory.state import Task, TaskStatus


def _make_task(task_id: str, depends_on: list[str] | None = None,
               rollup_sources: list[str] | None = None,
               status: TaskStatus = TaskStatus.TODO,
               priority: int = 2) -> Task:
    """Build a Task object for test use."""
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        status=status,
        depends_on=depends_on or [],
        rollup_sources=rollup_sources or [],
        priority=priority,
    )


def test_has_cycle_returns_false_for_acyclic_graph():
    """Build graph with T1→T2→T3 (T2 depends on T1, T3 depends on T2).
    Assert graph.has_cycle() is False."""
    t1 = _make_task("T1")
    t2 = _make_task("T2", depends_on=["T1"])
    t3 = _make_task("T3", depends_on=["T2"])
    graph = DependencyGraph([t1, t2, t3])
    assert graph.has_cycle() is False


def test_has_cycle_returns_true_for_direct_cycle():
    """Build graph where T1 depends on T2 AND T2 depends on T1.
    Assert graph.has_cycle() is True."""
    t1 = _make_task("T1", depends_on=["T2"])
    t2 = _make_task("T2", depends_on=["T1"])
    graph = DependencyGraph([t1, t2])
    assert graph.has_cycle() is True


def test_has_cycle_returns_true_for_transitive_cycle():
    """T1 depends on T2, T2 depends on T3, T3 depends on T1.
    Assert graph.has_cycle() is True."""
    t1 = _make_task("T1", depends_on=["T2"])
    t2 = _make_task("T2", depends_on=["T3"])
    t3 = _make_task("T3", depends_on=["T1"])
    graph = DependencyGraph([t1, t2, t3])
    assert graph.has_cycle() is True
