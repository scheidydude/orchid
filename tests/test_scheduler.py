"""Tests for orchid/scheduler.py - Task scheduling engine.

Covers:
  - DependencyGraph: cycle detection, topological sort, ready/blocked queries
  - ParallelGroupDetector: parallel group computation
  - Scheduler: full schedule, caching, sequential/parallel convenience methods
  - Convenience functions: schedule_tasks, next_runnable_task, has_cycles, build_dependency_graph
"""

from __future__ import annotations

import threading
import time

import pytest

from orchid.memory.state import Task, TaskStatus

from orchid.scheduler import (
    DependencyGraph,
    ParallelGroupDetector,
    Scheduler,
    ScheduleResult,
    build_dependency_graph,
    has_cycles,
    next_runnable_task,
    schedule_tasks,
)


def task(id: str, status: TaskStatus = TaskStatus.TODO,
         priority: int = 2, depends_on: list[str] | None = None,
         rollup_sources: list[str] | None = None, **kwargs) -> Task:
    return Task(
        id=id,
        title=f"Task {id}",
        status=status,
        priority=priority,
        depends_on=depends_on or [],
        rollup_sources=rollup_sources or [],
        **kwargs,
    )


class TestDependencyGraphBuild:
    """Verify the dependency graph is constructed correctly."""

    def test_no_deps(self):
        tasks = [task("T1"), task("T2"), task("T3")]
        g = DependencyGraph(tasks)
        assert g.tasks == {t.id: t for t in tasks}
        assert g._deps == {"T1": set(), "T2": set(), "T3": set()}

    def test_single_dep(self):
        tasks = [task("T1"), task("T2", depends_on=["T1"])]
        g = DependencyGraph(tasks)
        assert g._deps["T2"] == {"T1"}
        assert "T2" in g._dependents["T1"]

    def test_multiple_deps(self):
        tasks = [
            task("T1"),
            task("T2"),
            task("T3", depends_on=["T1", "T2"]),
        ]
        g = DependencyGraph(tasks)
        assert g._deps["T3"] == {"T1", "T2"}
        assert "T3" in g._dependents["T1"]
        assert "T3" in g._dependents["T2"]

    def test_rollup_sources_included(self):
        tasks = [
            task("T1"),
            task("T2", rollup_sources=["T1"]),
        ]
        g = DependencyGraph(tasks)
        assert g._deps["T2"] == {"T1"}
        assert "T2" in g._dependents["T1"]

    def test_combined_deps(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T2"], rollup_sources=["T1"]),
        ]
        g = DependencyGraph(tasks)
        assert g._deps["T3"] == {"T1", "T2"}


class TestDependencyGraphCycleDetection:
    """Cycle detection via DFS."""

    def test_no_cycles_linear(self):
        tasks = [task("T1"), task("T2", depends_on=["T1"]), task("T3", depends_on=["T2"])]
        g = DependencyGraph(tasks)
        assert g.detect_cycles() == []

    def test_no_cycles_diamond(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T1"]),
            task("T4", depends_on=["T2", "T3"]),
        ]
        g = DependencyGraph(tasks)
        assert g.detect_cycles() == []

    def test_simple_cycle(self):
        tasks = [
            task("T1", depends_on=["T2"]),
            task("T2", depends_on=["T1"]),
        ]
        g = DependencyGraph(tasks)
        cycles = g.detect_cycles()
        assert len(cycles) == 1
        cycle = cycles[0]
        assert cycle[0] == cycle[-1]
        assert set(cycle) == {"T1", "T2"}

    def test_self_cycle(self):
        tasks = [task("T1", depends_on=["T1"])]
        g = DependencyGraph(tasks)
        cycles = g.detect_cycles()
        assert len(cycles) == 1
        assert cycles[0] == ["T1", "T1"]

    def test_longer_cycle(self):
        tasks = [
            task("T1", depends_on=["T3"]),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T2"]),
        ]
        g = DependencyGraph(tasks)
        cycles = g.detect_cycles()
        assert len(cycles) == 1
        assert set(cycles[0]) == {"T1", "T2", "T3"}

    def test_partial_cycle(self):
        """Only a subset of tasks form a cycle; others are acyclic."""
        tasks = [
            task("T1", depends_on=["T2"]),
            task("T2", depends_on=["T1"]),
            task("T3"),
            task("T4", depends_on=["T3"]),
        ]
        g = DependencyGraph(tasks)
        cycles = g.detect_cycles()
        assert len(cycles) == 1
        assert set(cycles[0]) == {"T1", "T2"}


class TestDependencyGraphTopologicalSort:
    """Kahn's algorithm for topological ordering."""

    def test_no_deps_returns_all(self):
        tasks = [task("T1"), task("T2"), task("T3")]
        g = DependencyGraph(tasks)
        result = g.topological_sort(set())
        assert sorted(result) == ["T1", "T2", "T3"]

    def test_linear_order(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T2"]),
        ]
        g = DependencyGraph(tasks)
        result = g.topological_sort(set())
        assert result == ["T1", "T2", "T3"]

    def test_diamond_order(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T1"]),
            task("T4", depends_on=["T2", "T3"]),
        ]
        g = DependencyGraph(tasks)
        result = g.topological_sort(set())
        assert result[0] == "T1"
        assert result[-1] == "T4"
        assert "T2" in result
        assert "T3" in result
        assert result.index("T2") > result.index("T1")
        assert result.index("T3") > result.index("T1")
        assert result.index("T4") > result.index("T2")
        assert result.index("T4") > result.index("T3")

    def test_skips_done(self):
        tasks = [
            task("T1", status=TaskStatus.DONE),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T2"]),
        ]
        g = DependencyGraph(tasks)
        result = g.topological_sort({"T1"})
        assert result == ["T2", "T3"]

    def test_skips_skipped(self):
        tasks = [
            task("T1", status=TaskStatus.SKIPPED),
            task("T2", depends_on=["T1"]),
        ]
        g = DependencyGraph(tasks)
        result = g.topological_sort({"T1"})
        assert result == ["T2"]

    def test_priority_ordering(self):
        """Higher priority (lower number) tasks come first among ready tasks."""
        tasks = [
            task("T1", priority=3),
            task("T2", priority=1),
            task("T3", priority=2, depends_on=["T1", "T2"]),
        ]
        g = DependencyGraph(tasks)
        result = g.topological_sort(set())
        assert result[0] == "T2"
        assert result[1] == "T1"
        assert result[2] == "T3"

    def test_excludes_non_todo(self):
        tasks = [
            task("T1", status=TaskStatus.DONE),
            task("T2", status=TaskStatus.IN_PROGRESS),
            task("T3", status=TaskStatus.TODO),
        ]
        g = DependencyGraph(tasks)
        result = g.topological_sort({"T1"})
        assert result == ["T3"]

    def test_empty_task_list(self):
        g = DependencyGraph([])
        assert g.topological_sort(set()) == []


class TestDependencyGraphReadyTasks:
    """get_ready_tasks returns tasks whose deps are all completed."""

    def test_all_ready(self):
        tasks = [task("T1"), task("T2")]
        g = DependencyGraph(tasks)
        ready = g.get_ready_tasks(set())
        assert set(t.id for t in ready) == {"T1", "T2"}

    def test_none_ready(self):
        """T1 depends on T2 (not completed), T2 has no deps so T2 IS ready."""
        tasks = [
            task("T1", depends_on=["T2"]),
            task("T2"),
        ]
        g = DependencyGraph(tasks)
        ready = g.get_ready_tasks(set())
        # T2 has no deps, so it is ready. T1 depends on T2 which is not completed.
        assert {t.id for t in ready} == {"T2"}

    def test_some_ready(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3"),
        ]
        g = DependencyGraph(tasks)
        ready = g.get_ready_tasks(set())
        assert {t.id for t in ready} == {"T1", "T3"}

    def test_only_todo_returned(self):
        tasks = [
            task("T1", status=TaskStatus.DONE),
            task("T2", status=TaskStatus.TODO, depends_on=["T1"]),
        ]
        g = DependencyGraph(tasks)
        ready = g.get_ready_tasks({"T1"})
        assert ready == [tasks[1]]

    def test_rollup_sources_count(self):
        """When T1 is completed, both T1 (no deps) and T2 (rollup dep satisfied) are ready."""
        tasks = [
            task("T1"),
            task("T2", rollup_sources=["T1"]),
        ]
        g = DependencyGraph(tasks)
        ready = g.get_ready_tasks({"T1"})
        # T1 is still TODO with no deps, so it's ready. T2's rollup dep is satisfied.
        assert {t.id for t in ready} == {"T1", "T2"}


class TestDependencyGraphBlockedTasks:
    """get_blocked_tasks returns tasks with unresolvable deps."""

    def test_no_blocked(self):
        tasks = [task("T1"), task("T2", depends_on=["T1"])]
        g = DependencyGraph(tasks)
        blocked = g.get_blocked_tasks({"T1"})
        assert blocked == []

    def test_blocked_by_missing_dep(self):
        tasks = [task("T1", depends_on=["MISSING"])]
        g = DependencyGraph(tasks)
        blocked = g.get_blocked_tasks(set())
        assert len(blocked) == 1
        assert blocked[0].id == "T1"

    def test_not_blocked_by_pending_dep(self):
        """A task waiting on a TODO task is NOT blocked."""
        tasks = [task("T1", depends_on=["T2"]), task("T2")]
        g = DependencyGraph(tasks)
        blocked = g.get_blocked_tasks(set())
        assert blocked == []

    def test_done_tasks_excluded(self):
        tasks = [task("T1", status=TaskStatus.DONE, depends_on=["MISSING"])]
        g = DependencyGraph(tasks)
        blocked = g.get_blocked_tasks({"T1"})
        assert blocked == []


class TestParallelGroupDetector:
    """Parallel group computation."""

    def test_no_parallelism_single_chain(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T2"]),
        ]
        g = DependencyGraph(tasks)
        detector = ParallelGroupDetector(g)
        groups = detector.compute_groups(tasks, set())
        assert len(groups) == 3
        assert [t.id for t in groups[0]] == ["T1"]
        assert [t.id for t in groups[1]] == ["T2"]
        assert [t.id for t in groups[2]] == ["T3"]

    def test_first_batch_parallel(self):
        tasks = [
            task("T1"),
            task("T2"),
            task("T3", depends_on=["T1", "T2"]),
        ]
        g = DependencyGraph(tasks)
        detector = ParallelGroupDetector(g)
        groups = detector.compute_groups(tasks, set())
        assert len(groups) == 2
        assert set(t.id for t in groups[0]) == {"T1", "T2"}
        assert [t.id for t in groups[1]] == ["T3"]

    def test_diamond_groups(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T1"]),
            task("T4", depends_on=["T2", "T3"]),
        ]
        g = DependencyGraph(tasks)
        detector = ParallelGroupDetector(g)
        groups = detector.compute_groups(tasks, set())
        assert len(groups) == 3
        assert groups[0][0].id == "T1"
        assert set(t.id for t in groups[1]) == {"T2", "T3"}
        assert groups[2][0].id == "T4"

    def test_done_tasks_excluded(self):
        tasks = [
            task("T1", status=TaskStatus.DONE),
            task("T2"),
            task("T3", depends_on=["T1", "T2"]),
        ]
        g = DependencyGraph(tasks)
        detector = ParallelGroupDetector(g)
        groups = detector.compute_groups(tasks, {"T1"})
        assert len(groups) == 2
        assert [t.id for t in groups[0]] == ["T2"]
        assert [t.id for t in groups[1]] == ["T3"]

    def test_empty(self):
        g = DependencyGraph([])
        detector = ParallelGroupDetector(g)
        groups = detector.compute_groups([], set())
        assert groups == []

    def test_priority_ordering_within_group(self):
        """Tasks within a group are sorted by priority (lower = higher priority)."""
        tasks = [
            task("T1", priority=3),
            task("T2", priority=1),
            task("T3", priority=2),
        ]
        g = DependencyGraph(tasks)
        detector = ParallelGroupDetector(g)
        groups = detector.compute_groups(tasks, set())
        assert len(groups) == 1
        assert [t.id for t in groups[0]] == ["T2", "T3", "T1"]

    def test_multiple_batches(self):
        """Three-level dependency with independent branch produces four groups."""
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T2"]),
            task("T4"),
            task("T5", depends_on=["T3", "T4"]),
        ]
        g = DependencyGraph(tasks)
        detector = ParallelGroupDetector(g)
        groups = detector.compute_groups(tasks, set())
        # Group 0: T1, T4 (both ready)
        # Group 1: T2 (depends on T1)
        # Group 2: T3 (depends on T2)
        # Group 3: T5 (depends on T3 and T4)
        assert len(groups) == 4
        assert set(t.id for t in groups[0]) == {"T1", "T4"}
        assert [t.id for t in groups[1]] == ["T2"]
        assert [t.id for t in groups[2]] == ["T3"]
        assert [t.id for t in groups[3]] == ["T5"]


class TestSchedulerSchedule:
    """Full schedule computation."""

    def test_empty_task_list(self):
        s = Scheduler([])
        result = s.schedule()
        assert result.ordered == []
        assert result.parallel_groups == []
        assert result.blocked_tasks == []
        assert result.skipped_tasks == []
        assert result.cycles == []
        assert result.total == 0

    def test_simple_chain(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T2"]),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert [t.id for t in result.ordered] == ["T1", "T2", "T3"]
        assert result.runnable_count == 3

    def test_with_cycles(self):
        tasks = [
            task("T1", depends_on=["T2"]),
            task("T2", depends_on=["T1"]),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert len(result.cycles) == 1

    def test_skipped_tasks_included(self):
        tasks = [
            task("T1", status=TaskStatus.SKIPPED),
            task("T2"),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert len(result.skipped_tasks) == 1
        assert result.skipped_tasks[0].id == "T1"

    def test_custom_completed_ids(self):
        tasks = [
            task("T1", status=TaskStatus.DONE),
            task("T2", depends_on=["T1"]),
        ]
        s = Scheduler(tasks)
        result = s.schedule(completed_ids={"T1"})
        assert result.ordered == [tasks[1]]

    def test_total_count(self):
        """total = ordered + blocked + skipped (not DONE)."""
        tasks = [
            task("T1"),
            task("T2", status=TaskStatus.DONE),
            task("T3", status=TaskStatus.SKIPPED),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        # T1 is ordered, T2 is DONE (not counted), T3 is skipped
        assert result.total == 2

    def test_parallel_groups_populated(self):
        tasks = [
            task("T1"),
            task("T2"),
            task("T3", depends_on=["T1", "T2"]),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert len(result.parallel_groups) == 2
        assert set(t.id for t in result.parallel_groups[0]) == {"T1", "T2"}


class TestSchedulerCaching:
    """Schedule result caching."""

    def test_cache_hit(self):
        tasks = [task("T1"), task("T2")]
        s = Scheduler(tasks)
        r1 = s.schedule()
        r2 = s.schedule()
        assert r1 is r2

    def test_cache_miss_on_force(self):
        tasks = [task("T1")]
        s = Scheduler(tasks)
        r1 = s.schedule()
        r2 = s.schedule(force=True)
        assert r1 is not r2

    def test_cache_reset(self):
        tasks = [task("T1")]
        s = Scheduler(tasks)
        s.schedule()
        s.reset_cache()
        r = s.schedule()
        assert r.ordered == [tasks[0]]

    def test_cache_invalidation_on_completed_ids_change(self):
        tasks = [task("T1"), task("T2", depends_on=["T1"])]
        s = Scheduler(tasks)
        r1 = s.schedule(completed_ids=set())
        r2 = s.schedule(completed_ids={"T1"})
        assert r1 is not r2


class TestSchedulerConvenienceMethods:
    """next_task, next_parallel_batch, get_ready_tasks, get_blocked_tasks, detect_cycles."""

    def test_next_task(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
        ]
        s = Scheduler(tasks)
        next_t = s.next_task()
        assert next_t.id == "T1"

    def test_next_task_none_when_all_done(self):
        tasks = [task("T1", status=TaskStatus.DONE)]
        s = Scheduler(tasks)
        assert s.next_task() is None

    def test_next_parallel_batch(self):
        tasks = [
            task("T1"),
            task("T2"),
            task("T3", depends_on=["T1", "T2"]),
        ]
        s = Scheduler(tasks)
        batch = s.next_parallel_batch()
        assert len(batch) == 2
        assert {t.id for t in batch} == {"T1", "T2"}

    def test_next_parallel_batch_empty(self):
        """T1 depends on T2; T2 has no deps so it's ready and will be in the batch."""
        tasks = [
            task("T1", depends_on=["T2"]),
            task("T2"),
        ]
        s = Scheduler(tasks)
        batch = s.next_parallel_batch()
        # T2 is ready (no deps), T1 is not (depends on T2)
        assert len(batch) == 1
        assert batch[0].id == "T2"

    def test_get_ready_tasks(self):
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
        ]
        s = Scheduler(tasks)
        ready = s.get_ready_tasks()
        assert ready == [tasks[0]]

    def test_get_blocked_tasks(self):
        tasks = [task("T1", depends_on=["MISSING"])]
        s = Scheduler(tasks)
        blocked = s.get_blocked_tasks()
        assert len(blocked) == 1
        assert blocked[0].id == "T1"

    def test_detect_cycles(self):
        tasks = [task("T1", depends_on=["T2"]), task("T2", depends_on=["T1"])]
        s = Scheduler(tasks)
        cycles = s.detect_cycles()
        assert len(cycles) == 1


class TestSchedulerThreadSafety:
    """Scheduler._lock protects concurrent schedule() calls."""

    def test_concurrent_schedule_calls(self):
        tasks = [task(f"T{i}") for i in range(10)]
        s = Scheduler(tasks)

        results = []
        errors = []

        def schedule_once():
            try:
                results.append(s.schedule())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=schedule_once) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 10
        for r in results:
            assert r is results[0] or r.ordered == results[0].ordered


class TestConvenienceFunctions:
    """schedule_tasks, next_runnable_task, has_cycles, build_dependency_graph."""

    def test_schedule_tasks(self):
        tasks = [task("T1"), task("T2", depends_on=["T1"])]
        result = schedule_tasks(tasks)
        assert [t.id for t in result.ordered] == ["T1", "T2"]

    def test_schedule_tasks_with_completed(self):
        tasks = [task("T1", status=TaskStatus.DONE), task("T2", depends_on=["T1"])]
        result = schedule_tasks(tasks, completed_ids={"T1"})
        assert result.ordered == [tasks[1]]

    def test_next_runnable_task(self):
        tasks = [
            task("T1", priority=3),
            task("T2", priority=1),
        ]
        t = next_runnable_task(tasks)
        assert t.id == "T2"

    def test_next_runnable_task_none(self):
        tasks = [task("T1", status=TaskStatus.DONE)]
        assert next_runnable_task(tasks) is None

    def test_has_cycles_true(self):
        tasks = [task("T1", depends_on=["T2"]), task("T2", depends_on=["T1"])]
        assert has_cycles(tasks) is True

    def test_has_cycles_false(self):
        tasks = [task("T1"), task("T2", depends_on=["T1"])]
        assert has_cycles(tasks) is False

    def test_build_dependency_graph(self):
        tasks = [task("T1"), task("T2", depends_on=["T1"])]
        g = build_dependency_graph(tasks)
        assert isinstance(g, DependencyGraph)
        assert g._deps["T2"] == {"T1"}


class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_single_task(self):
        tasks = [task("T1")]
        s = Scheduler(tasks)
        result = s.schedule()
        assert result.ordered == [tasks[0]]
        assert len(result.parallel_groups) == 1
        assert result.parallel_groups[0] == [tasks[0]]

    def test_task_with_both_depends_on_and_rollup_sources(self):
        tasks = [
            task("T1"),
            task("T2"),
            task("T3", depends_on=["T1"], rollup_sources=["T2"]),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert result.ordered.index(tasks[2]) > result.ordered.index(tasks[0])
        assert result.ordered.index(tasks[2]) > result.ordered.index(tasks[1])

    def test_cycle_in_partial_graph(self):
        """Some tasks form a cycle, others don't."""
        tasks = [
            task("T1", depends_on=["T2"]),
            task("T2", depends_on=["T1"]),
            task("T3"),
            task("T4", depends_on=["T3"]),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert len(result.cycles) == 1
        ordered_ids = [t.id for t in result.ordered]
        assert "T3" in ordered_ids
        assert "T4" in ordered_ids

    def test_all_done_tasks(self):
        """DONE tasks are not counted in total."""
        tasks = [
            task("T1", status=TaskStatus.DONE),
            task("T2", status=TaskStatus.DONE),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert result.ordered == []
        assert result.runnable_count == 0
        # total = ordered(0) + blocked(0) + skipped(0) = 0
        assert result.total == 0

    def test_all_skipped_tasks(self):
        tasks = [task("T1", status=TaskStatus.SKIPPED)]
        s = Scheduler(tasks)
        result = s.schedule()
        assert result.ordered == []
        assert len(result.skipped_tasks) == 1

    def test_blocked_by_both_missing_and_pending(self):
        """A task depends on a missing task and a pending task."""
        tasks = [
            task("T1", depends_on=["MISSING", "T2"]),
            task("T2"),
        ]
        s = Scheduler(tasks)
        blocked = s.get_blocked_tasks()
        assert len(blocked) == 1
        assert blocked[0].id == "T1"

    def test_rollup_as_only_dependency(self):
        tasks = [
            task("T1"),
            task("T2", rollup_sources=["T1"]),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert result.ordered.index(tasks[1]) > result.ordered.index(tasks[0])

    def test_schedule_with_large_task_set(self):
        """Performance sanity check with many tasks."""
        tasks = [task(f"T{i}") for i in range(100)]
        s = Scheduler(tasks)
        start = time.monotonic()
        result = s.schedule()
        elapsed = time.monotonic() - start
        assert result.runnable_count == 100
        assert elapsed < 5.0

    def test_schedule_with_complex_diamond(self):
        """Two-level diamond: T1 -> T2, T3 -> T4 (both depend on T2 and T3)."""
        tasks = [
            task("T1"),
            task("T2", depends_on=["T1"]),
            task("T3", depends_on=["T1"]),
            task("T4", depends_on=["T2", "T3"]),
            task("T5"),
            task("T6", depends_on=["T4", "T5"]),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        ordered_ids = [t.id for t in result.ordered]
        assert ordered_ids.index("T1") < ordered_ids.index("T2")
        assert ordered_ids.index("T1") < ordered_ids.index("T3")
        assert ordered_ids.index("T2") < ordered_ids.index("T4")
        assert ordered_ids.index("T3") < ordered_ids.index("T4")
        assert ordered_ids.index("T4") < ordered_ids.index("T6")
        assert ordered_ids.index("T5") < ordered_ids.index("T6")
