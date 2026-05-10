"""Tests for WorkerPool and resource limits — Phase 3."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from orchid.subprocess_runner import WorkerPool, _PoolWorker, _child_cpu, _apply_resource_limits
from orchid.worker_protocol import TaskContext, WorkerResult


def _ctx(task_id: str = "T001") -> TaskContext:
    return TaskContext(
        task_id=task_id,
        task_description="do stuff",
        session_context="ctx",
        agent_type="base",
        model_key="local",
        project_dir="/tmp/proj",
        injection_queue_path="/tmp/queue",
    )


class TestChildCpu:
    def test_returns_float(self):
        val = _child_cpu()
        assert isinstance(val, float)
        assert val >= 0.0

    def test_delta_non_negative(self):
        before = _child_cpu()
        after = _child_cpu()
        assert after >= before


class TestApplyResourceLimits:
    def test_does_not_raise_on_linux(self):
        """_apply_resource_limits must not crash regardless of OS."""
        mock_cfg = MagicMock()
        mock_cfg.get.return_value = {"max_as_gb": 4, "max_cpu_s": 600, "max_files": 256}
        # May raise ValueError/PermissionError on some kernels — that's caught internally
        _apply_resource_limits(mock_cfg)

    def test_import_error_is_silent(self):
        """Windows-style ImportError from 'resource' module must not raise."""
        import sys
        mock_cfg = MagicMock()
        mock_cfg.get.return_value = {}
        # Temporarily hide the resource module
        resource_mod = sys.modules.pop("resource", None)
        try:
            _apply_resource_limits(mock_cfg)  # must not raise
        finally:
            if resource_mod is not None:
                sys.modules["resource"] = resource_mod


class TestPoolWorkerMocked:
    def _make_worker_with_stdout(self, lines: list[str]) -> _PoolWorker:
        """Create a _PoolWorker whose subprocess stdout is pre-loaded."""
        import io
        w = _PoolWorker.__new__(_PoolWorker)
        w._lock = threading.Lock()
        w._ready = True
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        # readline() needs a file-like object, not an iterator
        mock_proc.stdout = io.StringIO("".join(lines))
        mock_proc.stdin = MagicMock()
        w._proc = mock_proc
        return w

    def test_run_task_returns_success(self):
        result_json = WorkerResult(task_id="T001", success=True, result="ok").to_json()
        w = self._make_worker_with_stdout([result_json + "\n", '{"type":"ready"}\n'])
        result = w.run_task(_ctx(), None, timeout_s=5.0)
        assert result.success is True
        assert result.result == "ok"

    def test_run_task_calls_stream_callback(self):
        event = json.dumps({"type": "agent_step", "task_id": "T001", "thought": "hi"})
        result_json = WorkerResult(task_id="T001", success=True, result="ok").to_json()
        w = self._make_worker_with_stdout([event + "\n", result_json + "\n", '{"type":"ready"}\n'])
        events = []
        result = w.run_task(_ctx(), events.append, timeout_s=5.0)
        assert result.success is True
        assert any("hi" in json.dumps(e) for e in events)

    def test_run_task_dead_proc_returns_error(self):
        w = _PoolWorker.__new__(_PoolWorker)
        w._lock = threading.Lock()
        w._ready = True
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin.write.side_effect = OSError("broken pipe")
        mock_proc.stdin.flush = MagicMock()
        w._proc = mock_proc
        result = w.run_task(_ctx(), None, timeout_s=1.0)
        assert result.success is False
        assert "stdin error" in result.error


class TestWorkerPoolMocked:
    def _mock_pool(self, size: int = 2) -> WorkerPool:
        pool = WorkerPool.__new__(WorkerPool)
        pool._size = size
        pool._semaphore = threading.Semaphore(size)
        pool._lock = threading.Lock()
        pool._closed = False
        pool._workers = []
        return pool

    def test_submit_uses_available_worker(self):
        pool = self._mock_pool(size=1)
        w = MagicMock()
        w.alive.return_value = True
        w._lock = threading.Lock()
        w.run_task.return_value = WorkerResult(task_id="T001", success=True, result="done")
        pool._workers = [w]

        result = pool.submit(_ctx())
        assert result.success is True
        w.run_task.assert_called_once()

    def test_shutdown_closes_all_workers(self):
        pool = self._mock_pool(size=2)
        w1, w2 = MagicMock(), MagicMock()
        pool._workers = [w1, w2]
        pool.shutdown()
        w1.close.assert_called_once()
        w2.close.assert_called_once()
        assert pool._closed is True

    def test_no_available_worker_returns_error(self):
        pool = self._mock_pool(size=1)
        pool._workers = []  # no workers
        result = pool.submit(_ctx())
        assert result.success is False
        assert "No available" in result.error
