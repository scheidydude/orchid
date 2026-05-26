import os
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests-only")

import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from orchid.auth.jwt import hash_password, issue_access_token
from orchid.auth.store import UserStore
from orchid.auth.types import User


@pytest.fixture
def tmp_store(tmp_path):
    store = UserStore(path=tmp_path / "users.json")
    store.add_user(User(user_id="u1", username="alice", role="user", password_hash=hash_password("pw")))
    store.add_user(User(user_id="admin1", username="admin", role="admin", password_hash=hash_password("pw")))
    return store


@pytest.fixture
def app_client(tmp_store, tmp_path):
    runs_file = tmp_path / "runs.jsonl"

    def _fake_run_store_init(self, runs_file=None, **kw):
        self._file = runs_file or (tmp_path / "runs.jsonl")
        self._lock = threading.Lock()

    mock_engine = MagicMock()

    with (
        patch("orchid.auth.store.get_store", return_value=tmp_store),
        patch("orchid.cron.store.TaskRunStore.__init__", _fake_run_store_init),
        patch("orchid.cron.engine.get_engine", return_value=mock_engine),
    ):
        from orchid.interfaces.web_server import create_app
        app = create_app(project_paths=[])
        yield TestClient(app)


def auth_header(user_id: str) -> dict:
    user = User(user_id=user_id, username=user_id)
    return {"Authorization": f"Bearer {issue_access_token(user)}"}


VALID_TASK_BODY = {
    "name": "Daily Echo",
    "schedule": "0 9 * * *",
    "task_type": "shell",
    "config": {"command": "echo hi"},
}


class TestSchedulerTaskCRUD:

    def test_list_tasks_empty(self, app_client):
        response = app_client.get("/api/scheduler/tasks", headers=auth_header("u1"))
        assert response.status_code == 200
        assert response.json()["tasks"] == []

    def test_create_task(self, app_client):
        response = app_client.post(
            "/api/scheduler/tasks",
            json=VALID_TASK_BODY,
            headers=auth_header("u1"),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["task_id"].startswith("stask_")
        assert data["owner_id"] == "u1"

    def test_create_task_missing_name_returns_400(self, app_client):
        body = {"schedule": "0 9 * * *", "task_type": "shell", "config": {}}
        response = app_client.post(
            "/api/scheduler/tasks",
            json=body,
            headers=auth_header("u1"),
        )
        assert response.status_code == 400

    def test_create_task_invalid_type_returns_400(self, app_client):
        body = {**VALID_TASK_BODY, "task_type": "bad_type"}
        response = app_client.post(
            "/api/scheduler/tasks",
            json=body,
            headers=auth_header("u1"),
        )
        assert response.status_code == 400

    def test_get_task(self, app_client):
        create_resp = app_client.post(
            "/api/scheduler/tasks",
            json=VALID_TASK_BODY,
            headers=auth_header("u1"),
        )
        assert create_resp.status_code == 201
        task_id = create_resp.json()["task_id"]

        response = app_client.get(
            f"/api/scheduler/tasks/{task_id}",
            headers=auth_header("u1"),
        )
        assert response.status_code == 200
        assert response.json()["task_id"] == task_id

    def test_get_nonexistent_task_returns_404(self, app_client):
        response = app_client.get(
            "/api/scheduler/tasks/stask_missing",
            headers=auth_header("u1"),
        )
        assert response.status_code == 404

    def test_update_task(self, app_client):
        create_resp = app_client.post(
            "/api/scheduler/tasks",
            json=VALID_TASK_BODY,
            headers=auth_header("u1"),
        )
        task_id = create_resp.json()["task_id"]

        update_resp = app_client.put(
            f"/api/scheduler/tasks/{task_id}",
            json={**VALID_TASK_BODY, "name": "Updated"},
            headers=auth_header("u1"),
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "Updated"

    def test_delete_task(self, app_client):
        create_resp = app_client.post(
            "/api/scheduler/tasks",
            json=VALID_TASK_BODY,
            headers=auth_header("u1"),
        )
        task_id = create_resp.json()["task_id"]

        del_resp = app_client.delete(
            f"/api/scheduler/tasks/{task_id}",
            headers=auth_header("u1"),
        )
        assert del_resp.status_code == 200

        get_resp = app_client.get(
            f"/api/scheduler/tasks/{task_id}",
            headers=auth_header("u1"),
        )
        assert get_resp.status_code == 404

    def test_run_now_returns_queued(self, app_client):
        create_resp = app_client.post(
            "/api/scheduler/tasks",
            json=VALID_TASK_BODY,
            headers=auth_header("u1"),
        )
        task_id = create_resp.json()["task_id"]

        run_resp = app_client.post(
            f"/api/scheduler/tasks/{task_id}/run",
            headers=auth_header("u1"),
        )
        assert run_resp.status_code == 200
        assert run_resp.json()["queued"] is True

    def test_list_runs_empty(self, app_client):
        create_resp = app_client.post(
            "/api/scheduler/tasks",
            json=VALID_TASK_BODY,
            headers=auth_header("u1"),
        )
        task_id = create_resp.json()["task_id"]

        runs_resp = app_client.get(
            f"/api/scheduler/tasks/{task_id}/runs",
            headers=auth_header("u1"),
        )
        assert runs_resp.status_code == 200
        assert runs_resp.json()["runs"] == []

    def test_user_cannot_see_other_users_task(self, app_client, tmp_store):
        create_resp = app_client.post(
            "/api/scheduler/tasks",
            json=VALID_TASK_BODY,
            headers=auth_header("u1"),
        )
        task_id = create_resp.json()["task_id"]

        # Add u2
        tmp_store.add_user(User(user_id="u2", username="bob", role="user", password_hash=hash_password("pw")))

        get_resp = app_client.get(
            f"/api/scheduler/tasks/{task_id}",
            headers=auth_header("u2"),
        )
        # 404 if auth passes but task not found; 403 if middleware rejects u2
        assert get_resp.status_code in {403, 404}

    def test_admin_sees_all_tasks(self, app_client):
        app_client.post(
            "/api/scheduler/tasks",
            json=VALID_TASK_BODY,
            headers=auth_header("u1"),
        )

        list_resp = app_client.get(
            "/api/scheduler/tasks",
            headers=auth_header("admin1"),
        )
        assert list_resp.status_code == 200
        tasks = list_resp.json()["tasks"]
        assert len(tasks) >= 1
        assert any(t["owner_id"] == "u1" for t in tasks)

    def test_unauthenticated_returns_401_or_403(self, app_client):
        response = app_client.get("/api/scheduler/tasks")
        assert response.status_code in {401, 403}
