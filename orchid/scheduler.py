"""orchid/scheduler.py — Task scheduling engine.

Responsibilities:
  - Resolve task dependencies and detect cycles (T017)
  - Compute execution order respecting priorities and dependencies
  - Identify sets of tasks that can run in parallel
  - Provide a scheduling API for both sequential and parallel dispatch

Architecture:
  D0017: Task dependencies — tasks declare `depends_on` and `rollup_sources`.
         The scheduler respects these when ordering tasks.
  D0021: Parallelism — the scheduler identifies independent task sets that
         can be dispatched concurrently.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from orchid.memory.state import Task, TaskStatus

logger = logging.getLogger(__name__)


# ── Scheduling result ──────────────────────────────────────────────────────────


@dataclass
class ScheduleResult:
    """The output of a scheduling pass."""
    ordered: list[Task] = field(default_factory=list)
    parallel_groups: list[list[Task]] = field(default_factory=list)
    blocked_tasks: list[Task] = field(default_factory=list)
    skipped_tasks: list[Task] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.ordered) + len(self.blocked_tasks) + len(self.skipped_tasks)

    @property
    def runnable_count(self) -> int:
        return len(self.ordered)


# ── Dependency graph ───────────────────────────────────────────────────────────


class DependencyGraph:
    """Directed graph of task dependencies for cycle detection and topological ordering."""

    def __init__(self, tasks: list[Task]) -> None:
        self.tasks: dict[str, Task] = {t.id: t for t in tasks}
        self._deps: dict[str, set[str]] = {}
        self._dependents: dict[str, set[str]] = defaultdict(set)
        self._build(tasks)

    def _build(self, tasks: list[Task]) -> None:
        for task in self.tasks.values():
            all_deps: set[str] = set(task.depends_on) | set(task.rollup_sources)
            self._deps[task.id] = all_deps
            for dep in all_deps:
                self._dependents[dep].add(task.id)

    def detect_cycles(self) -> list[list[str]]:
        """Return list of cycle paths using DFS."""
        task_map = self.tasks
        cycles: list[list[str]] = []
        visited: set[str] = set()

        def dfs(task_id: str, path: list[str]) -> None:
            if task_id in path:
                cycle_start = path.index(task_id)
                cycles.append(path[cycle_start:] + [task_id])
                return
            if task_id in visited:
                return
            visited.add(task_id)
            task = task_map.get(task_id)
            if task:
                for dep in self._deps.get(task_id, set()):
                    dfs(dep, path + [task_id])

        for task in self.tasks.values():
            if task.id not in visited:
                dfs(task.id, [])
        return cycles

    def topological_sort(self, completed_ids: set[str]) -> list[str]:
        """
        Return task IDs in dependency-respecting order, skipping completed tasks.
        Uses Kahn's algorithm for a deterministic ordering.
        """
        pending = {
            tid for tid, task in self.tasks.items()
            if task.status == TaskStatus.TODO and tid not in completed_ids
        }

        valid_deps = {
            tid for tid in pending
            if self._deps.get(tid, set()).issubset(completed_ids | pending)
        }
        pending = valid_deps

        in_degree: dict[str, int] = {}
        for tid in pending:
            in_degree[tid] = len(self._deps.get(tid, set()) & pending)

        queue: deque[str] = deque(
            tid for tid in pending if in_degree[tid] == 0
        )
        queue = deque(sorted(queue, key=lambda tid: self.tasks[tid].priority))

        result: list[str] = []
        while queue:
            tid = queue.popleft()
            result.append(tid)
            for dependent in sorted(
                self._dependents.get(tid, set()),
                key=lambda d: self.tasks[d].priority,
            ):
                if dependent in pending:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        return result

    def get_ready_tasks(self, completed_ids: set[str]) -> list[Task]:
        """Return tasks whose dependencies are all satisfied."""
        ready: list[Task] = []
        for task in self.tasks.values():
            if task.status != TaskStatus.TODO:
                continue
            all_deps = set(task.depends_on) | set(task.rollup_sources)
            if all_deps.issubset(completed_ids):
                ready.append(task)
        return ready

    def get_blocked_tasks(self, completed_ids: set[str]) -> list[Task]:
        """Return tasks that cannot run because of unresolvable dependencies."""
        blocked: list[Task] = []
        for task in self.tasks.values():
            if task.status != TaskStatus.TODO:
                continue
            all_deps = set(task.depends_on) | set(task.rollup_sources)
            unresolvable = all_deps - completed_ids - {tid for tid in self.tasks}
            if unresolvable:
                blocked.append(task)
        return blocked


# ── Parallel group detector ────────────────────────────────────────────────────


class ParallelGroupDetector:
    """Identifies sets of tasks that can be dispatched in parallel."""

    def __init__(self, graph: DependencyGraph) -> None:
        self._graph = graph

    def compute_groups(
        self,
        tasks: list[Task],
        completed_ids: set[str],
    ) -> list[list[Task]]:
        """
        Partition runnable TODO tasks into parallel groups.

        Tasks in the same group have no dependency relationship between them
        and can be dispatched concurrently.  Tasks in later groups depend on
        at least one task in an earlier group.
        """
        graph = self._graph
        pending: dict[str, Task] = {
            tid: task for tid, task in graph.tasks.items()
            if task.status == TaskStatus.TODO and tid not in completed_ids
        }

        ready: set[str] = set()
        for tid, task in pending.items():
            unresolved = graph._deps.get(tid, set()) - completed_ids
            if not unresolved:
                ready.add(tid)

        groups: list[list[Task]] = []
        resolved: set[str] = set(completed_ids)

        while ready:
            group = sorted(ready, key=lambda tid: pending[tid].priority)
            groups.append([pending[tid] for tid in group])
            resolved |= ready

            next_ready: set[str] = set()
            for tid in ready:
                for dependent in graph._dependents.get(tid, set()):
                    if dependent in pending and dependent not in ready and dependent not in resolved:
                        unresolved = graph._deps.get(dependent, set()) - resolved
                        if not unresolved:
                            next_ready.add(dependent)
            ready = next_ready

        return groups


# ── Scheduler ──────────────────────────────────────────────────────────────────


class Scheduler:
    """
    Orchestrates task execution order and parallel dispatch.

    Usage:
        scheduler = Scheduler(session.tasks)
        result = scheduler.schedule()

        # Sequential execution:
        for task in result.ordered:
            execute(task)

        # Parallel dispatch:
        for group in result.parallel_groups:
            dispatch_concurrently(group)
    """

    def __init__(self, tasks: list[Task]) -> None:
        self.tasks = tasks
        self._graph: DependencyGraph | None = None
        self._detector: ParallelGroupDetector | None = None
        self._schedule_cache: ScheduleResult | None = None
        self._cache_key: str = ""
        self._lock = threading.Lock()

    @property
    def graph(self) -> DependencyGraph:
        if self._graph is None:
            self._graph = DependencyGraph(self.tasks)
        return self._graph

    @property
    def detector(self) -> ParallelGroupDetector:
        if self._detector is None:
            self._detector = ParallelGroupDetector(self.graph)
        return self._detector

    def schedule(
        self,
        completed_ids: set[str] | None = None,
        force: bool = False,
    ) -> ScheduleResult:
        """
        Compute the full schedule for the current task set.

        Args:
            completed_ids: Set of task IDs already completed. If None,
                           derives from task statuses.
            force: Ignore cached result and recompute.

        Returns:
            ScheduleResult with ordered tasks, parallel groups, and blocked tasks.
        """
        with self._lock:
            if completed_ids is None:
                completed_ids = {
                    t.id for t in self.tasks
                    if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED)
                }

            cache_key = self._make_cache_key(completed_ids)
            if not force and self._schedule_cache is not None and self._cache_key == cache_key:
                return self._schedule_cache

            result = self._compute_schedule(completed_ids)
            self._schedule_cache = result
            self._cache_key = cache_key
            return result

    def next_task(self) -> Task | None:
        """
        Return the single next task to execute (highest priority, dependency-ready).
        This is the sequential equivalent of session.next_task().
        """
        result = self.schedule()
        if result.ordered:
            return result.ordered[0]
        return None

    def next_parallel_batch(self) -> list[Task]:
        """
        Return the next batch of tasks that can run in parallel.
        Returns empty list if no parallel tasks are available.
        """
        result = self.schedule()
        if result.parallel_groups:
            return result.parallel_groups[0]
        return []

    def get_ready_tasks(self) -> list[Task]:
        """Return all tasks whose dependencies are satisfied."""
        completed_ids = {
            t.id for t in self.tasks
            if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED)
        }
        return self.graph.get_ready_tasks(completed_ids)

    def get_blocked_tasks(self) -> list[Task]:
        """Return tasks blocked by unresolvable dependencies."""
        completed_ids = {
            t.id for t in self.tasks
            if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED)
        }
        return self.graph.get_blocked_tasks(completed_ids)

    def detect_cycles(self) -> list[list[str]]:
        """Detect circular dependencies in the task graph."""
        return self.graph.detect_cycles()

    def reset_cache(self) -> None:
        """Clear the schedule cache (call after tasks are modified)."""
        with self._lock:
            self._schedule_cache = None
            self._cache_key = ""

    def _compute_schedule(self, completed_ids: set[str]) -> ScheduleResult:
        graph = self.graph
        detector = self.detector

        cycles = graph.detect_cycles()

        topo_order = graph.topological_sort(completed_ids)
        ordered = [graph.tasks[tid] for tid in topo_order if tid in graph.tasks]

        parallel_groups = detector.compute_groups(self.tasks, completed_ids)

        blocked = graph.get_blocked_tasks(completed_ids)

        skipped = [t for t in self.tasks if t.status == TaskStatus.SKIPPED]

        return ScheduleResult(
            ordered=ordered,
            parallel_groups=parallel_groups,
            blocked_tasks=blocked,
            skipped_tasks=skipped,
            cycles=cycles,
        )

    def _make_cache_key(self, completed_ids: set[str]) -> str:
        parts = []
        for t in self.tasks:
            parts.append(f"{t.id}={t.status.value}")
        parts.append(f"done={','.join(sorted(completed_ids))}")
        return "|".join(parts)


# ── Convenience functions ──────────────────────────────────────────────────────


def schedule_tasks(
    tasks: list[Task],
    completed_ids: set[str] | None = None,
) -> ScheduleResult:
    """
    One-shot scheduling function.

    Args:
        tasks: List of Task objects.
        completed_ids: Set of already-completed task IDs.

    Returns:
        ScheduleResult with ordered, parallel, and blocked tasks.
    """
    scheduler = Scheduler(tasks)
    return scheduler.schedule(completed_ids)


def next_runnable_task(tasks: list[Task]) -> Task | None:
    """
    Pick the highest-priority runnable TODO task.

    This is a convenience wrapper around Scheduler.next_task() for
    code that doesn't want to create a Scheduler instance.
    """
    scheduler = Scheduler(tasks)
    return scheduler.next_task()


def has_cycles(tasks: list[Task]) -> bool:
    """Return True if the task graph contains circular dependencies."""
    graph = DependencyGraph(tasks)
    return len(graph.detect_cycles()) > 0


def build_dependency_graph(tasks: list[Task]) -> DependencyGraph:
    """Build and return a DependencyGraph from a list of tasks."""
    return DependencyGraph(tasks)
