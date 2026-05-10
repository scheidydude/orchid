"""Tests for BackgroundRunner.graceful_shutdown() — Phase 1."""

import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from orchid.runner import BackgroundRunner, _ProjectState


class TestGracefulShutdown:
    def _make_runner(self):
        r = BackgroundRunner.__new__(BackgroundRunner)
        r._lock = threading.Lock()
        r._states = {}
        r._sem_lock = threading.Lock()
        r._semaphores = {}
        r._provider_concurrency = {}
        return r

    def test_shutdown_sets_global_event(self):
        from orchid.shutdown import is_shutting_down, clear
        r = self._make_runner()
        r.graceful_shutdown(timeout_s=0.1)
        assert is_shutting_down()

    def test_shutdown_signals_all_cancel_events(self):
        r = self._make_runner()
        ev1, ev2 = threading.Event(), threading.Event()
        r._states["p1"] = _ProjectState(cancel_event=ev1)
        r._states["p2"] = _ProjectState(cancel_event=ev2)
        r.graceful_shutdown(timeout_s=0.1)
        assert ev1.is_set()
        assert ev2.is_set()

    def test_shutdown_waits_for_futures(self):
        r = self._make_runner()
        done = threading.Event()

        def slow_task():
            time.sleep(0.05)
            done.set()

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as ex:
            f = ex.submit(slow_task)
        state = _ProjectState(future=f)
        r._states["proj"] = state

        result = r.graceful_shutdown(timeout_s=2.0)
        assert result is True
        assert done.is_set()

    def test_shutdown_returns_false_on_timeout(self):
        r = self._make_runner()
        from concurrent.futures import ThreadPoolExecutor
        ex = ThreadPoolExecutor(max_workers=1)
        blocker = threading.Event()
        f = ex.submit(lambda: blocker.wait(timeout=10))
        state = _ProjectState(future=f)
        r._states["proj"] = state

        result = r.graceful_shutdown(timeout_s=0.05)
        assert result is False
        blocker.set()
        ex.shutdown(wait=False)

    def test_shutdown_ignores_already_done_futures(self):
        r = self._make_runner()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as ex:
            f = ex.submit(lambda: None)
        f.result()  # ensure done
        state = _ProjectState(future=f)
        r._states["proj"] = state

        result = r.graceful_shutdown(timeout_s=0.1)
        assert result is True


class TestMarkerFile:
    def test_marker_written_on_start(self, tmp_path):
        r = BackgroundRunner.__new__(BackgroundRunner)
        r._lock = threading.Lock()
        r._states = {}
        r._sem_lock = threading.Lock()
        r._semaphores = {}
        r._provider_concurrency = {}
        r._executor = MagicMock()
        r._executor.submit = MagicMock(return_value=MagicMock())

        with patch.object(r, "_run"):
            marker = r._marker_path(str(tmp_path))
            assert not marker.exists()
            r._write_marker(str(tmp_path))
            assert marker.exists()

    def test_marker_removed_on_clean_exit(self, tmp_path):
        r = BackgroundRunner.__new__(BackgroundRunner)
        r._write_marker(str(tmp_path))
        assert r._marker_path(str(tmp_path)).exists()
        r._remove_marker(str(tmp_path))
        assert not r._marker_path(str(tmp_path)).exists()

    def test_remove_missing_marker_is_safe(self, tmp_path):
        r = BackgroundRunner.__new__(BackgroundRunner)
        r._remove_marker(str(tmp_path))  # must not raise
