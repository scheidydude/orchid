"""Tests for Phase 4 backend additions.

Covers:
  - GET /api/auth/users returns budget_usd, cpu_budget_seconds, projects, created_at
  - PUT /api/auth/users/{id} accepts budget_usd
"""

from __future__ import annotations

import os
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-admin-phase4")


@pytest.fixture()
def admin_client(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import orchid.interfaces.web_server as ws
    from orchid.auth.store import FileUserStore as UserStore
    from orchid.auth.audit import AuditStore
    from orchid.auth.jwt import hash_password
    from orchid.auth.types import User
    import orchid.auth.store as store_mod

    ws._projects.clear()
    ws._managers.clear()
    ws._runners.clear()
    ws._main_loop = None
    ws._auth_store = None
    ws._audit_store = None

    new_store = UserStore(path=tmp_path / "users.json")
    new_audit = AuditStore(audit_dir=tmp_path / "audit")
    ws._auth_store = new_store
    ws._audit_store = new_audit
    store_mod._store_instance = new_store

    # Admin user
    admin = User(
        user_id="admin1", username="admin", role="admin",
        is_active=True, password_hash=hash_password("adminpass"),
        email="admin@test.com",
    )
    new_store.add_user(admin)

    # Regular user with pre-set budgets + projects
    alice = User(
        user_id="user1", username="alice", role="user",
        is_active=True, password_hash=hash_password("alicepass"),
        email="alice@test.com",
        budget_usd=5.0, cpu_budget_seconds=3600.0, projects=["proj-a"],
    )
    new_store.add_user(alice)

    app = ws.create_app([])
    client = TestClient(app, raise_server_exceptions=True)

    # Log in as admin (sets cookies)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "adminpass"})
    assert r.status_code == 200, r.text

    yield client, new_store


class TestUserListExpanded:
    def test_includes_budget_usd(self, admin_client):
        client, _ = admin_client
        r = client.get("/api/auth/users")
        assert r.status_code == 200
        alice = next(u for u in r.json()["users"] if u["username"] == "alice")
        assert alice["budget_usd"] == 5.0

    def test_includes_cpu_budget_seconds(self, admin_client):
        client, _ = admin_client
        r = client.get("/api/auth/users")
        assert r.status_code == 200
        alice = next(u for u in r.json()["users"] if u["username"] == "alice")
        assert alice["cpu_budget_seconds"] == 3600.0

    def test_includes_projects(self, admin_client):
        client, _ = admin_client
        r = client.get("/api/auth/users")
        assert r.status_code == 200
        alice = next(u for u in r.json()["users"] if u["username"] == "alice")
        assert alice["projects"] == ["proj-a"]

    def test_includes_created_at(self, admin_client):
        client, _ = admin_client
        r = client.get("/api/auth/users")
        assert r.status_code == 200
        alice = next(u for u in r.json()["users"] if u["username"] == "alice")
        assert alice["created_at"] is not None


class TestUserUpdateBudget:
    def test_update_budget_usd(self, admin_client):
        client, store = admin_client
        r = client.put("/api/auth/users/user1", json={"budget_usd": 20.0})
        assert r.status_code == 200
        assert store.get_user("user1").budget_usd == 20.0

    def test_update_cpu_budget_seconds(self, admin_client):
        client, store = admin_client
        r = client.put("/api/auth/users/user1", json={"cpu_budget_seconds": 7200.0})
        assert r.status_code == 200
        assert store.get_user("user1").cpu_budget_seconds == 7200.0

    def test_update_budget_and_role_together(self, admin_client):
        client, store = admin_client
        r = client.put("/api/auth/users/user1", json={"budget_usd": 50.0, "role": "readonly"})
        assert r.status_code == 200
        u = store.get_user("user1")
        assert u.budget_usd == 50.0
        assert u.role == "readonly"

    def test_update_unknown_user_404(self, admin_client):
        client, _ = admin_client
        r = client.put("/api/auth/users/nobody", json={"budget_usd": 1.0})
        assert r.status_code == 404

    def test_budget_zero_accepted(self, admin_client):
        """0.0 = unlimited — must not be rejected."""
        client, store = admin_client
        r = client.put("/api/auth/users/user1", json={"budget_usd": 0.0})
        assert r.status_code == 200
        assert store.get_user("user1").budget_usd == 0.0
