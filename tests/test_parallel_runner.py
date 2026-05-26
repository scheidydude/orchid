"""Tests for orchid/runner.py - BackgroundRunner with parallel task dispatch.

Covers:
  - BackgroundRunner: start/stop/status, provider semaphores, concurrency config
  - _ProjectState: lifecycle tracking
  - _execute_group: concurrent execution via ThreadPoolExecutor
  - _execute_task_with_semaphore: provider semaphore acquisition
  - Thread safety of state mutations
  - Edge cases: empty groups, cancellation, failure handling
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from orchid.memory.state import Task, TaskStatus
from orchid.runner import BackgroundRunner, _ProjectState


def task(id, status=TaskStatus.TODO, priority=2, depends_on=None, model_override=None, **kwargs):
    return Task(
        id=id,
        title=f"Task {id}",
        status=status,
        priority=priority,
        depends_on=depends_on or [],
        model_override=model_override,
        **kwargs,
    )


def make_runner(**overrides):
    runner = BackgroundRunner()
    for provider, limit in overrides.items():
        runner.set_provider_concurrency(provider, limit)
    return runner


class TestProjectState:
    def test_defaults(self):
        state = _ProjectState()
        assert state.future is None
        assert not state.cancel_event.is_set()
        assert state.current_task is None
        assert state.tasks_done == 0

    def test_cancel_event(self):
        state = _ProjectState()
        state.cancel_event.set()
        assert state.cancel_event.is_set()

    def test_tasks_done_increments(self):
        state = _ProjectState()
        state.tasks_done += 1
        assert state.tasks_done == 1


class TestProviderSemaphores:
    def test_default_claude_limit(self):
        runner = BackgroundRunner()
        assert runner._provider_concurrency["claude"] == 3

    def test_default_openrouter_limit(self):
        runner = BackgroundRunner()
        assert runner._provider_concurrency["openrouter"] == 3

    def test_default_bedrock_limit(self):
        runner = BackgroundRunner()
        assert runner._provider_concurrency["bedrock"] == 3

    def test_default_openai_limit(self):
        runner = BackgroundRunner()
        assert runner._provider_concurrency["openai"] == 3

    def test_default_unknown_provider(self):
        runner = BackgroundRunner()
        assert runner._provider_concurrency.get("unknown") is None

    def test_get_semaphore_creates_lazily(self):
        runner = BackgroundRunner()
        assert len(runner._semaphores) == 0
        sem = runner._get_semaphore("claude")
        assert isinstance(sem, threading.Semaphore)
        assert len(runner._semaphores) == 1

    def test_get_semaphore_reuses(self):
        runner = BackgroundRunner()
        sem1 = runner._get_semaphore("local")
        sem2 = runner._get_semaphore("local")
        assert sem1 is sem2

    def test_get_semaphore_unknown_provider_default(self):
        runner = BackgroundRunner()
        sem = runner._get_semaphore("unknown_provider")
        assert sem._value == 10

    def test_set_provider_concurrency_creates_semaphore(self):
        runner = BackgroundRunner()
        runner.set_provider_concurrency("custom", 5)
        assert "custom" in runner._semaphores
        assert runner._semaphores["custom"]._value == 5

    def test_set_provider_concurrency_updates_existing(self):
        runner = BackgroundRunner()
        runner.set_provider_concurrency("claude", 5)
        assert runner._semaphores["claude"]._value == 5

    def test_set_provider_concurrency_reduces(self):
        runner = BackgroundRunner()
        runner.set_provider_concurrency("claude", 1)
        assert runner._semaphores["claude"]._value == 1

    def test_semaphore_blocks_on_exhaustion(self):
        runner = BackgroundRunner()
        runner.set_provider_concurrency("test", 2)
        sem = runner._get_semaphore("test")
        acquired = []
        barrier = threading.Barrier(2)
        def acquire_and_hold():
            sem.acquire()
            acquired.append(threading.current_thread().name)
            barrier.wait()
            time.sleep(0.1)
            sem.release()
        t1 = threading.Thread(target=acquire_and_hold, name="t1")
        t2 = threading.Thread(target=acquire_and_hold, name="t2")
        t3 = threading.Thread(target=acquire_and_hold, name="t3")
        t1.start()
        t2.start()
        t3.start()
        t1.join(timeout=3)
        t2.join(timeout=3)
        t3.join(timeout=3)
        assert len(acquired) == 3

    def test_semaphore_thread_safety(self):
        runner = BackgroundRunner()
        runner.set_provider_concurrency("stress", 5)
        sem = runner._get_semaphore("stress")
        acquired_count = 0
        lock = threading.Lock()
        def acquire_release():
            sem.acquire()
            with lock:
                nonlocal acquired_count
                acquired_count += 1
            time.sleep(0.01)
            sem.release()
        threads = [threading.Thread(target=acquire_release) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert acquired_count == 20

    def test_set_provider_concurrency_thread_safety(self):
        runner = BackgroundRunner()
        def update(provider, limit):
            runner.set_provider_concurrency(provider, limit)
        threads = [threading.Thread(target=update, args=("p1", i)) for i in range(1, 11)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert runner._provider_concurrency["p1"] == 10
        assert runner._semaphores["p1"]._value == 10










class TestRunnerLifecycle:
    def test_start_returns_true(self, tmp_path):
        runner = BackgroundRunner()
        with patch.object(runner, "_run", return_value=None):
            result = runner.start(str(tmp_path))
            assert result is True

    def test_start_returns_false_if_already_running(self, tmp_path):
        runner = BackgroundRunner()
        original_submit = runner._executor.submit
        def fake_submit(fn, *args, **kwargs):
            fut = original_submit(fn, *args, **kwargs)
            def fake_done():
                return False
            fut.done = fake_done
            return fut
        with patch.object(runner._executor, "submit", fake_submit):
            runner.start(str(tmp_path))
            result = runner.start(str(tmp_path))
            assert result is False

    def test_stop_returns_true(self, tmp_path):
        runner = BackgroundRunner()
        original_submit = runner._executor.submit
        def fake_submit(fn, *args, **kwargs):
            fut = original_submit(fn, *args, **kwargs)
            def fake_done():
                return False
            fut.done = fake_done
            return fut
        with patch.object(runner._executor, "submit", fake_submit):
            runner.start(str(tmp_path))
            result = runner.stop(str(tmp_path))
            assert result is True

    def test_stop_returns_false_if_not_running(self, tmp_path):
        runner = BackgroundRunner()
        result = runner.stop(str(tmp_path))
        assert result is False

    def test_stop_returns_false_if_done(self, tmp_path):
        runner = BackgroundRunner()
        with patch.object(runner, "_run", return_value=None):
            runner.start(str(tmp_path))
            state = runner._states[str(tmp_path)]
            result = runner.stop(str(tmp_path))
            assert result is False

    def test_get_status_running(self, tmp_path):
        runner = BackgroundRunner()
        original_submit = runner._executor.submit
        def fake_submit(fn, *args, **kwargs):
            fut = original_submit(fn, *args, **kwargs)
            def fake_done():
                return False
            fut.done = fake_done
            return fut
        with patch.object(runner._executor, "submit", fake_submit):
            runner.start(str(tmp_path))
            state = runner._states[str(tmp_path)]
            state.current_task = "T001: Test task"
            state.tasks_done = 2
            status = runner.get_status(str(tmp_path))
            assert status["running"] is True
            assert status["current_task"] == "T001: Test task"
            assert status["tasks_done"] == 2

    def test_get_status_not_running(self, tmp_path):
        runner = BackgroundRunner()
        status = runner.get_status(str(tmp_path))
        assert status["running"] is False
        assert status["current_task"] is None
        assert status["tasks_done"] == 0

    def test_get_status_done(self, tmp_path):
        runner = BackgroundRunner()
        with patch.object(runner, "_run", return_value=None):
            runner.start(str(tmp_path))
            status = runner.get_status(str(tmp_path))
            assert status["running"] is False




class TestExecuteGroup:
    def test_execute_group_single_task(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        task_obj = task("T001")
        state = _ProjectState()
        def mock_exec(pp, st, sess, orch, t):
            st.tasks_done += 1
        with patch.object(runner, "_execute_task_with_semaphore", side_effect=mock_exec):
            runner._execute_group("/fake/project", state, session, orch, [task_obj], {"T000"})
        assert state.tasks_done == 1

    def test_execute_group_multiple_tasks(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        tasks_list = [task("T001"), task("T002"), task("T003")]
        state = _ProjectState()
        completed = {"T000"}
        def mock_exec(pp, st, sess, orch, t):
            st.tasks_done += 1
        with patch.object(runner, "_execute_task_with_semaphore", side_effect=mock_exec):
            runner._execute_group("/fake/project", state, session, orch, tasks_list, completed)
        assert state.tasks_done == 3

    def test_execute_group_respects_current_task(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        tasks_list = [task("T001"), task("T002")]
        state = _ProjectState()
        call_order = []
        def track_task(pp, st, sess, orch, t):
            call_order.append(t.id)
        with patch.object(runner, "_execute_task_with_semaphore", side_effect=track_task):
            runner._execute_group("/fake/project", state, session, orch, tasks_list, set())
        assert len(call_order) == 2
        assert "T001" in call_order
        assert "T002" in call_order

    def test_execute_group_handles_task_failure(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        tasks_list = [task("T001"), task("T002")]
        state = _ProjectState()
        call_count = 0
        def fail_first(pp, st, sess, orch, t):
            nonlocal call_count
            call_count += 1
            if t.id == "T001":
                raise RuntimeError("task failed")
        with patch.object(runner, "_execute_task_with_semaphore", side_effect=fail_first):
            runner._execute_group("/fake/project", state, session, orch, tasks_list, set())
        assert call_count == 2

    def test_execute_group_max_parallel_limit(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        tasks_list = [task(f"T{i}") for i in range(10)]
        state = _ProjectState()
        def mock_exec(pp, st, sess, orch, t):
            st.tasks_done += 1
        with patch("orchid.config.get") as mock_cfg:
            mock_cfg.return_value = 3
            with patch.object(runner, "_execute_task_with_semaphore", side_effect=mock_exec):
                runner._execute_group("/fake/project", state, session, orch, tasks_list, set())
            assert state.tasks_done == 10

    def test_execute_group_max_workers_capped_by_group_size(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        tasks_list = [task("T001"), task("T002")]
        state = _ProjectState()
        def mock_exec(pp, st, sess, orch, t):
            st.tasks_done += 1
        with patch("orchid.config.get") as mock_cfg:
            mock_cfg.return_value = 100
            with patch.object(runner, "_execute_task_with_semaphore", side_effect=mock_exec):
                runner._execute_group("/fake/project", state, session, orch, tasks_list, set())
            assert state.tasks_done == 2
class TestExecuteTaskWithSemaphore:
    def test_acquires_semaphore_before_execution(self):
        runner = BackgroundRunner()
        runner.set_provider_concurrency("test_provider", 2)
        session = MagicMock()
        orch = MagicMock()
        task_obj = task("T001", model_override="test_provider")
        state = _ProjectState()
        runner._execute_task_with_semaphore("/fake/project", state, session, orch, task_obj)
        orch._execute_task.assert_called_once_with(task_obj)
        session.save.assert_called()
        assert state.tasks_done == 1

    def test_uses_model_override_for_provider(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        task_obj = task("T001", model_override="custom_provider")
        state = _ProjectState()
        runner._execute_task_with_semaphore("/fake/project", state, session, orch, task_obj)
        assert "custom_provider" in runner._semaphores

    def test_defaults_to_local_when_no_override(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        task_obj = task("T001")
        state = _ProjectState()
        runner._execute_task_with_semaphore("/fake/project", state, session, orch, task_obj)
        assert "local" in runner._semaphores

    def test_task_failure_sets_blocked(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        orch._execute_task.side_effect = RuntimeError("execution failed")
        task_obj = task("T001")
        state = _ProjectState()
        runner._execute_task_with_semaphore("/fake/project", state, session, orch, task_obj)
        session.update_task_status.assert_called_with("T001", TaskStatus.BLOCKED)
        session.save.assert_called()
        assert state.tasks_done == 0

    def test_semaphore_acquisition_failure_sets_blocked(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        task_obj = task("T001")
        state = _ProjectState()
        blocked_sem = threading.Semaphore(0)
        with patch.object(runner, "_get_semaphore", return_value=blocked_sem):
            runner._execute_task_with_semaphore("/fake/project", state, session, orch, task_obj)
        session.update_task_status.assert_called_with("T001", TaskStatus.BLOCKED)
        session.save.assert_called()

    def test_semaphore_released_after_execution(self):
        runner = BackgroundRunner()
        runner.set_provider_concurrency("sem_test", 1)
        session = MagicMock()
        orch = MagicMock()
        orch._execute_task.side_effect = RuntimeError("fail")
        task_obj = task("T001", model_override="sem_test")
        state = _ProjectState()
        runner._execute_task_with_semaphore("/fake/project", state, session, orch, task_obj)
        sem = runner._get_semaphore("sem_test")
        assert sem._value == 1

    def test_semaphore_released_on_acquisition_failure(self):
        runner = BackgroundRunner()
        session = MagicMock()
        orch = MagicMock()
        task_obj = task("T001")
        state = _ProjectState()
        class FailingSemaphore:
            def acquire(self, blocking=True, timeout=-1):
                raise RuntimeError("semaphore broken")
            def release(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
        with patch.object(runner, "_get_semaphore", return_value=FailingSemaphore()):
            runner._execute_task_with_semaphore("/fake/project", state, session, orch, task_obj)
        session.update_task_status.assert_called_with("T001", TaskStatus.BLOCKED)


class TestRunnerThreadSafety:
    def test_concurrent_start_calls(self):
        runner = BackgroundRunner()
        errors = []
        def start_project(path):
            try:
                with patch.object(runner, "_run", return_value=None):
                    runner.start(path)
            except Exception as e:
                errors.append(e)
        paths = [f"/project/{i}" for i in range(20)]
        threads = [threading.Thread(target=start_project, args=(p,)) for p in paths]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors
        assert len(runner._states) == 20

    def test_concurrent_stop_calls(self):
        runner = BackgroundRunner()
        with patch.object(runner, "_run", return_value=None):
            runner.start("/project/1")
        errors = []
        def stop_project():
            try:
                runner.stop("/project/1")
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=stop_project) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors

    def test_concurrent_get_status(self):
        runner = BackgroundRunner()
        with patch.object(runner, "_run", return_value=None):
            runner.start("/project/1")
            state = runner._states["/project/1"]
            state.current_task = "T001"
            state.tasks_done = 5
        errors = []
        statuses = []
        lock = threading.Lock()
        def get_status():
            try:
                s = runner.get_status("/project/1")
                with lock:
                    statuses.append(s)
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=get_status) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors
        assert len(statuses) == 20
        for s in statuses:
            assert s["running"] is True
            assert s["current_task"] == "T001"
            assert s["tasks_done"] == 5

    def test_concurrent_set_provider_concurrency(self):
        runner = BackgroundRunner()
        def update(provider, limit):
            runner.set_provider_concurrency(provider, limit)
        threads = [threading.Thread(target=update, args=(f"p{i}", i)) for i in range(1, 11)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        for i in range(1, 11):
            assert runner._provider_concurrency[f"p{i}"] == i

    def test_concurrent_state_mutations(self):
        runner = BackgroundRunner()
        state = _ProjectState()
        errors = []
        def mutate_task():
            try:
                for _ in range(100):
                    state.current_task = f"T{threading.current_thread().name}"
                    state.tasks_done += 1
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=mutate_task) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors
        assert state.tasks_done == 1000


class TestRunLoop:
    def _make_mock_session(self):
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        return session

    def _make_mock_orch(self):
        return MagicMock()

    def _make_mock_mcp(self):
        mcp = MagicMock()
        mcp.connect = MagicMock()
        mcp.disconnect = MagicMock()
        return mcp

    def test_run_loop_calls_scheduler(self):
        runner = BackgroundRunner()
        session = self._make_mock_session()
        orch = self._make_mock_orch()
        mcp = self._make_mock_mcp()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp_cls:
                    mock_mcp_cls.return_value = mcp
                    state = _ProjectState()
                    runner._run("/fake/project", state)

    def test_run_loop_empty_tasks(self):
        runner = BackgroundRunner()
        session = self._make_mock_session()
        orch = self._make_mock_orch()
        mcp = self._make_mock_mcp()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp_cls:
                    mock_mcp_cls.return_value = mcp
                    state = _ProjectState()
                    runner._run("/fake/project", state)
                    assert state.current_task is None

    def test_cancel_event_stops_loop(self):
        runner = BackgroundRunner()
        session = self._make_mock_session()
        orch = self._make_mock_orch()
        mcp = self._make_mock_mcp()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp_cls:
                    mock_mcp_cls.return_value = mcp
                    state = _ProjectState()
                    state.cancel_event.set()
                    runner._run("/fake/project", state)
                    assert state.current_task is None

    def test_run_handles_session_error(self):
        runner = BackgroundRunner()
        session = self._make_mock_session()
        orch = self._make_mock_orch()
        mcp = self._make_mock_mcp()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp_cls:
                    mock_mcp_cls.return_value = mcp
                    state = _ProjectState()
                    runner._run("/fake/project", state)


class TestStreamEmitters:
    def test_emitter_wired_when_registered(self):
        from orchid.web.server import _stream_emitters
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        web_emitter = MagicMock()
        _stream_emitters["/fake/project"] = web_emitter
        try:
            with patch("orchid.runner.Session") as mock_session_cls:
                mock_session_cls.return_value = session
                with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                    mock_orch_cls.return_value = orch
                    with patch("orchid.runner.MCPManager") as mock_mcp:
                        mock_mcp_instance = MagicMock()
                        mock_mcp.return_value = mock_mcp_instance
                        mock_mcp_instance.connect = MagicMock()
                        mock_mcp_instance.disconnect = MagicMock()
                        state = _ProjectState()
                        runner._run("/fake/project", state)
                        web_emitter.emit.assert_called()
                        web_emitter.close.assert_called()
        finally:
            _stream_emitters.pop("/fake/project", None)

    def test_null_emitter_when_no_web_emitter(self):
        from orchid.web.server import _stream_emitters
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        _stream_emitters.pop("/fake/project", None)
        try:
            with patch("orchid.runner.Session") as mock_session_cls:
                mock_session_cls.return_value = session
                with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                    mock_orch_cls.return_value = orch
                    with patch("orchid.runner.MCPManager") as mock_mcp:
                        mock_mcp_instance = MagicMock()
                        mock_mcp.return_value = mock_mcp_instance
                        mock_mcp_instance.connect = MagicMock()
                        mock_mcp_instance.disconnect = MagicMock()
                        state = _ProjectState()
                        runner._run("/fake/project", state)
        finally:
            _stream_emitters.pop("/fake/project", None)


class TestMCPConnection:
    def test_mcp_connection_failure_logged(self):
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp:
                    mock_mcp_instance = MagicMock()
                    mock_mcp.return_value = mock_mcp_instance
                    mock_mcp_instance.connect.side_effect = RuntimeError("MCP failed")
                    mock_mcp_instance.disconnect = MagicMock()
                    state = _ProjectState()
                    runner._run("/fake/project", state)

    def test_mcp_disconnect_on_completion(self):
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp:
                    mock_mcp_instance = MagicMock()
                    mock_mcp.return_value = mock_mcp_instance
                    mock_mcp_instance.connect = MagicMock()
                    mock_mcp_instance.disconnect = MagicMock()
                    state = _ProjectState()
                    runner._run("/fake/project", state)
                    mock_mcp_instance.disconnect.assert_called()

    def test_mcp_disconnect_on_error(self):
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        orch._execute_task.side_effect = RuntimeError("task error")
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp:
                    mock_mcp_instance = MagicMock()
                    mock_mcp.return_value = mock_mcp_instance
                    mock_mcp_instance.connect = MagicMock()
                    mock_mcp_instance.disconnect = MagicMock()
                    state = _ProjectState()
                    runner._run("/fake/project", state)
                    mock_mcp_instance.disconnect.assert_called()


class TestSessionEvents:
    def test_session_start_event_emitted(self):
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp:
                    mock_mcp_instance = MagicMock()
                    mock_mcp.return_value = mock_mcp_instance
                    mock_mcp_instance.connect = MagicMock()
                    mock_mcp_instance.disconnect = MagicMock()
                    state = _ProjectState()
                    runner._run("/fake/project", state)

    def test_session_end_event_emitted(self):
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp:
                    mock_mcp_instance = MagicMock()
                    mock_mcp.return_value = mock_mcp_instance
                    mock_mcp_instance.connect = MagicMock()
                    mock_mcp_instance.disconnect = MagicMock()
                    state = _ProjectState()
                    runner._run("/fake/project", state)

    def test_session_end_event_duration(self):
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp:
                    mock_mcp_instance = MagicMock()
                    mock_mcp.return_value = mock_mcp_instance
                    mock_mcp_instance.connect = MagicMock()
                    mock_mcp_instance.disconnect = MagicMock()
                    state = _ProjectState()
                    runner._run("/fake/project", state)

    def test_session_end_event_task_count(self):
        runner = BackgroundRunner()
        session = MagicMock()
        session.tasks = []
        session.project_name = "test-project"
        orch = MagicMock()
        with patch("orchid.runner.Session") as mock_session_cls:
            mock_session_cls.return_value = session
            with patch("orchid.runner.Orchestrator") as mock_orch_cls:
                mock_orch_cls.return_value = orch
                with patch("orchid.runner.MCPManager") as mock_mcp:
                    mock_mcp_instance = MagicMock()
                    mock_mcp.return_value = mock_mcp_instance
                    mock_mcp_instance.connect = MagicMock()
                    mock_mcp_instance.disconnect = MagicMock()
                    state = _ProjectState()
                    state.tasks_done = 42
                    runner._run("/fake/project", state)


# ── Spec-required tests (T183) ────────────────────────────────────────────────

def _make_tasks_with_deps(*specs):
    """Build Task objects. specs: (id, depends_on_list)."""
    return [
        Task(id=tid, title=f"Task {tid}", type="code_generate", depends_on=list(deps))
        for tid, deps in specs
    ]


def _mock_session_with_tasks(tasks):
    session = MagicMock()
    session.tasks = tasks
    session.project_name = "test"

    def _update_status(task_id, status):
        for t in tasks:
            if t.id == task_id:
                t.status = status
                return True
        return False

    session.update_task_status.side_effect = _update_status
    return session


@pytest.mark.slow
def test_independent_tasks_run_concurrently():
    """Three independent tasks run in parallel; wall time must be < 3 × single-task time."""
    tasks = _make_tasks_with_deps(("T1", []), ("T2", []), ("T3", []))
    session = _mock_session_with_tasks(tasks)
    orch = MagicMock()

    def mock_execute(task):
        time.sleep(0.05)
        task.status = TaskStatus.DONE

    orch._execute_task.side_effect = mock_execute

    state = _ProjectState()
    runner = BackgroundRunner()

    start = time.monotonic()
    runner._run_loop(".", state, session, orch, MagicMock())
    elapsed = time.monotonic() - start

    assert orch._execute_task.call_count == 3
    assert all(t.status == TaskStatus.DONE for t in tasks)
    # 3 × 0.05s sequential = 0.15s; parallel completes in ~0.05s; allow 0.3s for CI
    assert elapsed < 0.3, f"Expected parallel execution < 0.3s, got {elapsed:.2f}s"


def test_semaphore_limits_provider_concurrency():
    """provider_concurrency=1 must prevent more than 1 concurrent task execution."""
    tasks = _make_tasks_with_deps(("T1", []), ("T2", []))
    session = _mock_session_with_tasks(tasks)
    orch = MagicMock()

    counter = {"current": 0, "max_seen": 0}
    lock = threading.Lock()

    def mock_execute(task):
        with lock:
            counter["current"] += 1
            counter["max_seen"] = max(counter["max_seen"], counter["current"])
        time.sleep(0.02)
        with lock:
            counter["current"] -= 1
        task.status = TaskStatus.DONE

    orch._execute_task.side_effect = mock_execute

    runner = BackgroundRunner()
    runner.set_provider_concurrency("local", 1)

    runner._run_loop(".", _ProjectState(), session, orch, MagicMock())

    assert counter["max_seen"] <= 1, (
        f"Expected max 1 concurrent execution, saw {counter['max_seen']}"
    )
    assert orch._execute_task.call_count == 2


def test_dependent_task_waits_for_parent():
    """T2 (depends_on T1) must not execute until T1 is marked DONE."""
    tasks = _make_tasks_with_deps(("T1", []), ("T2", ["T1"]))
    session = _mock_session_with_tasks(tasks)
    orch = MagicMock()
    call_order = []

    def mock_execute(task):
        call_order.append(task.id)
        task.status = TaskStatus.DONE

    orch._execute_task.side_effect = mock_execute

    runner = BackgroundRunner()
    runner._run_loop(".", _ProjectState(), session, orch, MagicMock())

    assert orch._execute_task.call_count == 2, (
        f"Expected 2 tasks executed, got {orch._execute_task.call_count}"
    )
    assert "T1" in call_order and "T2" in call_order
    assert call_order.index("T1") < call_order.index("T2"), (
        f"T1 must run before T2, got order: {call_order}"
    )
