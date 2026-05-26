import time
from unittest.mock import MagicMock, patch
import pytest

from orchid.cron.engine import CronEngine, get_engine, reset_engine


@pytest.fixture(autouse=True)
def reset():
    reset_engine()
    yield
    reset_engine()


class TestCronEngineSingleton:
    def test_get_engine_returns_same_instance(self):
        e1 = get_engine()
        e2 = get_engine()
        assert e1 is e2

    def test_reset_engine_creates_new_instance(self):
        e1 = get_engine()
        reset_engine()
        e2 = get_engine()
        assert e1 is not e2


class TestCronEngineLifecycle:
    @patch("orchid.auth.store.get_store")
    def test_start_stop(self, mock_get_store):
        mock_store = MagicMock()
        mock_store.get_all_enabled_scheduled_tasks.return_value = []
        mock_get_store.return_value = mock_store

        e = CronEngine()
        # Trigger scheduler creation for u1 via add_or_update_task
        e.add_or_update_task(
            "u1",
            {
                "task_id": "placeholder",
                "schedule": "0 9 * * *",
                "enabled": True,
                "name": "T",
                "task_type": "shell",
                "config": {},
            },
        )
        assert e._schedulers["u1"].running is True
        e.stop()
        assert "u1" not in e._schedulers or e._schedulers.get("u1") is None

    @patch("orchid.auth.store.get_store")
    def test_start_registers_enabled_tasks(self, mock_get_store):
        task_dict = {
            "task_id": "stask_t1",
            "schedule": "0 9 * * *",
            "enabled": True,
            "name": "T",
            "task_type": "shell",
            "config": {"command": "echo hi"},
        }
        mock_store = MagicMock()
        mock_store.get_all_enabled_scheduled_tasks.return_value = [
            ("u1", task_dict),
        ]
        mock_get_store.return_value = mock_store

        engine = CronEngine()
        engine.start()
        assert engine._schedulers["u1"].get_job("stask_t1") is not None
        engine.stop()

    @patch("orchid.auth.store.get_store")
    def test_invalid_cron_expression_skips_task(self, mock_get_store):
        task_dict = {
            "task_id": "bad_task",
            "schedule": "not-a-cron",
            "enabled": True,
            "name": "Bad",
            "task_type": "shell",
            "config": {"command": "echo hi"},
        }
        mock_store = MagicMock()
        mock_store.get_all_enabled_scheduled_tasks.return_value = [
            ("u1", task_dict),
        ]
        mock_get_store.return_value = mock_store

        engine = CronEngine()
        # Should not raise an exception; invalid cron is logged and skipped
        engine.start()
        assert engine._schedulers["u1"].get_job("bad_task") is None
        engine.stop()


class TestCronEngineTaskManagement:
    @patch("orchid.auth.store.get_store")
    def test_add_or_update_task_enabled(self, mock_get_store):
        mock_store = MagicMock()
        mock_store.get_all_enabled_scheduled_tasks.return_value = []
        mock_get_store.return_value = mock_store

        engine = CronEngine()
        engine.start()
        engine.add_or_update_task(
            "u1",
            {
                "task_id": "stask_x",
                "schedule": "0 9 * * *",
                "enabled": True,
                "name": "T",
                "task_type": "shell",
                "config": {},
            },
        )
        assert engine._schedulers["u1"].get_job("stask_x") is not None
        engine.stop()

    @patch("orchid.auth.store.get_store")
    def test_add_or_update_task_disabled_removes_job(self, mock_get_store):
        mock_store = MagicMock()
        mock_store.get_all_enabled_scheduled_tasks.return_value = []
        mock_get_store.return_value = mock_store

        engine = CronEngine()
        engine.start()
        # Add enabled task
        engine.add_or_update_task(
            "u1",
            {
                "task_id": "stask_x",
                "schedule": "0 9 * * *",
                "enabled": True,
                "name": "T",
                "task_type": "shell",
                "config": {},
            },
        )
        assert engine._schedulers["u1"].get_job("stask_x") is not None
        # Disable it
        engine.add_or_update_task(
            "u1",
            {
                "task_id": "stask_x",
                "schedule": "0 9 * * *",
                "enabled": False,
                "name": "T",
                "task_type": "shell",
                "config": {},
            },
        )
        assert engine._schedulers["u1"].get_job("stask_x") is None
        engine.stop()

    @patch("orchid.auth.store.get_store")
    def test_remove_task(self, mock_get_store):
        mock_store = MagicMock()
        mock_store.get_all_enabled_scheduled_tasks.return_value = []
        mock_get_store.return_value = mock_store

        engine = CronEngine()
        engine.start()
        engine.add_or_update_task(
            "u1",
            {
                "task_id": "stask_x",
                "schedule": "0 9 * * *",
                "enabled": True,
                "name": "T",
                "task_type": "shell",
                "config": {},
            },
        )
        engine.remove_task("stask_x")
        assert engine._schedulers["u1"].get_job("stask_x") is None
        engine.stop()

    @patch.object(CronEngine, "_run_task")
    def test_run_now_calls_run_task_in_thread(self, mock_run_task):
        engine = CronEngine()
        engine.run_now(
            "u1",
            {
                "task_id": "t1",
                "name": "T",
            },
        )
        time.sleep(0.2)
        mock_run_task.assert_called_once_with("u1", {"task_id": "t1", "name": "T"})
