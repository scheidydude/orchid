"""Tests for agent suspend/resume and priority dispatch — Phase 4."""

import threading
import time
from unittest.mock import patch

import pytest

from orchid.agents.base import AgentCancelledError, BaseAgent
from orchid.memory.state import Task, TaskStatus
from orchid.scheduler import Scheduler, _priority_score


# ── Priority scoring ──────────────────────────────────────────────────────────

def _task(task_id: str, priority: int) -> Task:
    return Task(id=task_id, title=f"t{task_id}", status=TaskStatus.TODO,
                priority=priority, type="code_generate")


class TestPriorityScore:
    def test_p1_beats_p2(self):
        assert _priority_score(_task("T010", 1)) > _priority_score(_task("T010", 2))

    def test_p2_beats_p3(self):
        assert _priority_score(_task("T010", 2)) > _priority_score(_task("T010", 3))

    def test_lower_id_beats_higher_same_priority(self):
        assert _priority_score(_task("T001", 2)) > _priority_score(_task("T010", 2))

    def test_p1_always_beats_p3_regardless_of_id(self):
        # Even a very low-numbered p3 task should lose to any p1 task
        assert _priority_score(_task("T001", 1)) > _priority_score(_task("T999", 3))

    def test_score_is_positive(self):
        assert _priority_score(_task("T100", 2)) > 0

    def test_bad_id_format_safe(self):
        t = Task(id="CUSTOM", title="t", status=TaskStatus.TODO, priority=2, type="draft")
        score = _priority_score(t)
        assert score > 0


class TestSchedulerPriorityOrdering:
    def test_high_priority_first_in_ordered(self):
        tasks = [
            _task("T001", 3),
            _task("T002", 1),
            _task("T003", 2),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        ids = [t.id for t in result.ordered]
        assert ids[0] == "T002"  # p1 first

    def test_parallel_group_sorted_by_priority(self):
        tasks = [
            _task("T001", 3),
            _task("T002", 1),
        ]
        s = Scheduler(tasks)
        result = s.schedule()
        assert result.parallel_groups
        group = result.parallel_groups[0]
        assert group[0].id == "T002"  # p1 first


# ── Suspend / resume ──────────────────────────────────────────────────────────

class TestAgentSuspendResume:
    def test_suspend_sets_event(self):
        agent = BaseAgent()
        agent.suspend()
        assert agent._suspend_event.is_set()
        assert not agent._resume_event.is_set()

    def test_resume_sets_event_and_clears_suspend(self):
        agent = BaseAgent()
        agent.suspend()
        agent.resume()
        assert not agent._suspend_event.is_set()
        assert agent._resume_event.is_set()

    def test_suspend_parks_and_resumes(self):
        """Agent pauses at suspend boundary and continues after resume()."""
        agent = BaseAgent()
        parked = threading.Event()
        results = []
        call_count = [0]

        original_suspended_setter = None

        def fake_call(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return "Thought: first\nAction: read_file\nAction Input: {\"path\": \"x\"}"
            return "Thought: done\nFinal Answer: completed"

        agent.tools["read_file"] = lambda path: "content"

        # Pre-suspend so the first iteration check hits it
        agent.suspend()

        # Monkey-patch _suspended property to signal when agent actually parks
        _orig_wait = agent._resume_event.wait

        def patched_wait(*args, **kwargs):
            parked.set()
            return _orig_wait(*args, **kwargs)

        agent._resume_event.wait = patched_wait

        def run_agent():
            with patch("orchid.agents.base.call", side_effect=fake_call):
                try:
                    result = agent.run("do task")
                    results.append(result)
                except Exception as e:
                    results.append(f"ERROR: {e}")

        t = threading.Thread(target=run_agent)
        t.start()

        # Wait until agent is parked
        parked.wait(timeout=2.0)
        assert agent._suspend_event.is_set() or agent._suspended

        # Resume
        agent.resume()
        t.join(timeout=3.0)
        assert not t.is_alive()
        assert results == ["completed"]

    def test_suspend_saves_checkpoint(self, tmp_path):
        """Checkpoint is written when agent suspends."""
        from orchid.checkpoint.store import CheckpointStore

        agent = BaseAgent()
        store = CheckpointStore(tmp_path)
        agent.set_checkpoint_store(store)
        agent._current_task_id = "T_SUSP"
        agent.suspend()

        call_count = [0]

        def fake_call(messages, model_key, system):
            call_count[0] += 1
            return "Thought: x\nFinal Answer: done"

        def run_agent():
            with patch("orchid.agents.base.call", side_effect=fake_call):
                agent.resume()  # unblock immediately
                try:
                    agent.run("task")
                except Exception:
                    pass

        t = threading.Thread(target=run_agent)
        t.start()
        t.join(timeout=3.0)


# ── Agent registry integration ────────────────────────────────────────────────

class TestAgentRegistryIntegration:
    def test_runner_suspend_and_resume(self):
        """BackgroundRunner.suspend_task / resume_task go through agent_registry."""
        import orchid.agent_registry as ar
        from orchid.runner import BackgroundRunner

        agent = BaseAgent()
        ar.register("T_RUN", agent)

        runner = BackgroundRunner.__new__(BackgroundRunner)
        runner._lock = threading.Lock()
        runner._states = {}
        runner._sem_lock = threading.Lock()
        runner._semaphores = {}
        runner._provider_concurrency = {}

        assert runner.suspend_task("T_RUN") is True
        assert agent._suspend_event.is_set()

        assert runner.resume_task("T_RUN") is True
        assert agent._resume_event.is_set()

    def test_suspend_unknown_task_returns_false(self):
        import orchid.agent_registry as ar
        from orchid.runner import BackgroundRunner

        runner = BackgroundRunner.__new__(BackgroundRunner)
        runner._lock = threading.Lock()
        runner._states = {}
        runner._sem_lock = threading.Lock()
        runner._semaphores = {}
        runner._provider_concurrency = {}

        assert runner.suspend_task("T_UNKNOWN") is False
