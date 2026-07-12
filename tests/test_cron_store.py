"""Tests for TaskRunStore and UserStore scheduled-task methods."""

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from orchid.auth.store import UserStore
from orchid.auth.types import User
from orchid.cron.store import TaskRunStore
from orchid.cron.types import TaskRun

# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_runs_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "runs.jsonl"


@pytest.fixture
def run_store(tmp_runs_file: pathlib.Path) -> TaskRunStore:
    return TaskRunStore(runs_file=tmp_runs_file)


@pytest.fixture
def user_store(tmp_path: pathlib.Path) -> UserStore:
    return UserStore(path=tmp_path / "users.json")


@pytest.fixture
def user_with_store(user_store: UserStore) -> UserStore:
    u = User(user_id="u1", username="alice")
    user_store.add_user(u)
    return user_store


# ── TaskRunStore tests ────────────────────────────────────────────────────────

class TestTaskRunStore:

    def test_append_and_get_runs(self, run_store: TaskRunStore) -> None:
        run = TaskRun(task_id="t1", owner_id="u1", status="success")
        run_store.append(run)
        results = run_store.get_runs(task_id="t1")
        assert len(results) == 1
        assert results[0].task_id == "t1"

    def test_get_runs_filter_by_owner(self, run_store: TaskRunStore) -> None:
        run_store.append(TaskRun(task_id="t1", owner_id="u1"))
        run_store.append(TaskRun(task_id="t2", owner_id="u2"))
        results = run_store.get_runs(owner_id="u1")
        assert len(results) == 1
        assert results[0].owner_id == "u1"

    def test_get_runs_newest_first(self, run_store: TaskRunStore) -> None:
        base = datetime.now(UTC)
        for i in range(3):
            ts = base - timedelta(hours=i)
            run_store.append(TaskRun(task_id="t1", started_at=ts))
        results = run_store.get_runs(task_id="t1")
        assert len(results) == 3
        # Verify descending order of started_at
        for i in range(len(results) - 1):
            assert results[i].started_at >= results[i + 1].started_at

    def test_get_runs_limit(self, run_store: TaskRunStore) -> None:
        for i in range(10):
            run_store.append(TaskRun(task_id="t1"))
        results = run_store.get_runs(task_id="t1", limit=3)
        assert len(results) == 3

    def test_get_runs_empty_when_no_file(self) -> None:
        store = TaskRunStore(runs_file=pathlib.Path("/tmp/does_not_exist_xyz.jsonl"))
        results = store.get_runs()
        assert results == []

    def test_prune_removes_old_runs(self, tmp_runs_file: pathlib.Path) -> None:
        old_ts = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        with open(tmp_runs_file, "w", encoding="utf-8") as fh:
            fh.write(
                f'{{"run_id":"r1","task_id":"t1","owner_id":"u1","started_at":"{old_ts}","status":"success"}}\n'
            )
        store = TaskRunStore(runs_file=tmp_runs_file)
        results = store.get_runs()
        assert len(results) == 0

    def test_prune_keeps_recent_runs(self, tmp_runs_file: pathlib.Path) -> None:
        now_ts = datetime.now(UTC).isoformat()
        with open(tmp_runs_file, "w", encoding="utf-8") as fh:
            fh.write(
                f'{{"run_id":"r1","task_id":"t1","owner_id":"u1","started_at":"{now_ts}","status":"success"}}\n'
            )
        store = TaskRunStore(runs_file=tmp_runs_file)
        results = store.get_runs()
        assert len(results) == 1

    def test_append_never_raises(self, run_store: TaskRunStore, monkeypatch: pytest.MonkeyPatch) -> None:
        bad_path = pathlib.Path("/dev/null/bad/path")
        monkeypatch.setattr(run_store, "_file", bad_path)
        # Should not raise — append silently logs and continues
        run_store.append(TaskRun())


# ── UserStore scheduled-task tests ─────────────────────────────────────────────

class TestUserStoreScheduledTasks:

    def test_upsert_and_get(self, user_with_store: UserStore) -> None:
        user_with_store.upsert_scheduled_task(
            "u1",
            {"task_id": "stask_00000001", "name": "T", "enabled": True},
        )
        result = user_with_store.get_scheduled_task("u1", "stask_00000001")
        assert result is not None
        assert result["name"] == "T"

    def test_upsert_replaces_existing(self, user_with_store: UserStore) -> None:
        user_with_store.upsert_scheduled_task(
            "u1",
            {"task_id": "stask_00000001", "name": "Old", "enabled": True},
        )
        user_with_store.upsert_scheduled_task(
            "u1",
            {"task_id": "stask_00000001", "name": "New", "enabled": True},
        )
        result = user_with_store.get_scheduled_task("u1", "stask_00000001")
        assert result is not None
        assert result["name"] == "New"
        # User should have exactly 1 scheduled task
        user = user_with_store.get_user("u1")
        assert len(user.scheduled_tasks) == 1

    def test_delete_task(self, user_with_store: UserStore) -> None:
        user_with_store.upsert_scheduled_task(
            "u1",
            {"task_id": "stask_00000001", "name": "T", "enabled": True},
        )
        deleted = user_with_store.delete_scheduled_task("u1", "stask_00000001")
        assert deleted is True
        result = user_with_store.get_scheduled_task("u1", "stask_00000001")
        assert result is None

    def test_delete_nonexistent_returns_false(self, user_with_store: UserStore) -> None:
        result = user_with_store.delete_scheduled_task("u1", "stask_missing")
        assert result is False

    def test_get_all_enabled(self, user_with_store: UserStore) -> None:
        # Add second user u2
        u2 = User(user_id="u2", username="bob")
        user_with_store.add_user(u2)

        # Enabled task for u1
        user_with_store.upsert_scheduled_task(
            "u1",
            {"task_id": "stask_00000001", "name": "Enabled", "enabled": True},
        )
        # Disabled task for u2
        user_with_store.upsert_scheduled_task(
            "u2",
            {"task_id": "stask_00000002", "name": "Disabled", "enabled": False},
        )

        results = user_with_store.get_all_enabled_scheduled_tasks()
        assert len(results) == 1
        uid, task = results[0]
        assert uid == "u1"

    def test_scheduled_tasks_persisted(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "users.json"
        store1 = UserStore(path=path)
        u = User(user_id="u1", username="alice")
        store1.add_user(u)
        store1.upsert_scheduled_task(
            "u1",
            {"task_id": "stask_00000001", "name": "Persisted", "enabled": True},
        )

        # Create a NEW store from the same file
        store2 = UserStore(path=path)
        result = store2.get_scheduled_task("u1", "stask_00000001")
        assert result is not None
        assert result["name"] == "Persisted"
