from datetime import UTC, datetime
import pytest
from orchid.cron.types import ScheduledTask, TaskRun, _new_task_id, _new_run_id, _utcnow


class TestScheduledTask:
    def test_default_task_id_format(self):
        t = ScheduledTask()
        assert t.task_id.startswith("stask_")
        assert len(t.task_id) == 14

    def test_default_fields(self):
        t = ScheduledTask(owner_id="u1", name="Daily")
        assert t.enabled is True
        assert t.schedule == "0 9 * * *"
        assert t.task_type == "agent_prompt"
        assert t.config == {}
        assert t.notify_on_failure is True
        assert t.notify_on_success is False
        assert t.last_run_at is None
        assert t.last_run_status is None

    def test_unique_task_ids(self):
        ids = {_new_task_id() for _ in range(100)}
        assert len(ids) == 100

    def test_created_at_is_utc(self):
        t = ScheduledTask()
        assert t.created_at.tzinfo is not None

    def test_custom_schedule(self):
        t = ScheduledTask(schedule="*/5 * * * *")
        assert t.schedule == "*/5 * * * *"


class TestTaskRun:
    def test_default_run_id_format(self):
        r = TaskRun()
        assert r.run_id.startswith("run_")
        assert len(r.run_id) == 12

    def test_default_status_is_running(self):
        r = TaskRun()
        assert r.status == "running"

    def test_unique_run_ids(self):
        ids = {_new_run_id() for _ in range(100)}
        assert len(ids) == 100

    def test_fields_settable(self):
        r = TaskRun(task_id="t1", owner_id="u1", status="success", output="hello")
        assert r.task_id == "t1"
        assert r.status == "success"
        assert r.output == "hello"
