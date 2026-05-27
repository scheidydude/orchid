"""Tests for Phase 5 — BudgetGuard, vault_env_context, cost recording, reset endpoint.

Covers:
  - BudgetExceededError attributes
  - BudgetGuard.check(): unlimited (0), under budget, over budget, unknown user
  - BudgetGuard.record(): accumulates cost, persists to store, zero-cost ignored
  - BudgetGuard.remaining(): unlimited / partial / exhausted
  - vault_env_context: no vault key, provider vars injected, non-provider vars excluded
  - _compute_anthropic_cost: known model, unknown model
  - executor.execute: budget_exceeded → failure run, cost recorded after success
  - POST /api/admin/users/{id}/budget/reset endpoint
"""

from __future__ import annotations

import os
import threading
import pytest


# ── BudgetGuard unit tests ────────────────────────────────────────────────────

@pytest.fixture()
def user_store(tmp_path):
    pytest.importorskip("fastapi")
    os.environ.setdefault("JWT_SECRET", "test-budget-secret")
    from orchid.auth.store import FileUserStore
    from orchid.auth.jwt import hash_password
    from orchid.auth.types import User

    store = FileUserStore(path=tmp_path / "users.json")
    alice = User(
        user_id="alice", username="alice", role="user", is_active=True,
        password_hash=hash_password("pw"),
        budget_usd=10.0, budget_used_usd=0.0,
    )
    store.add_user(alice)
    return store


class TestBudgetGuardCheck:
    def test_unlimited_zero_budget_never_raises(self, user_store):
        from orchid.budget.guard import BudgetGuard
        store = user_store
        user = store.get_user("alice")
        user.budget_usd = 0.0
        store.update_user(user)
        guard = BudgetGuard("alice", store=store)
        guard.check()  # must not raise

    def test_under_budget_no_raise(self, user_store):
        from orchid.budget.guard import BudgetGuard
        store = user_store
        user = store.get_user("alice")
        user.budget_used_usd = 5.0
        store.update_user(user)
        guard = BudgetGuard("alice", store=store)
        guard.check()  # 5 < 10 → no raise

    def test_over_budget_raises(self, user_store):
        from orchid.budget.guard import BudgetGuard, BudgetExceededError
        store = user_store
        user = store.get_user("alice")
        user.budget_used_usd = 10.0
        store.update_user(user)
        guard = BudgetGuard("alice", store=store)
        with pytest.raises(BudgetExceededError) as exc_info:
            guard.check()
        assert exc_info.value.limit == 10.0
        assert exc_info.value.used == 10.0

    def test_unknown_user_no_raise(self, user_store):
        from orchid.budget.guard import BudgetGuard
        guard = BudgetGuard("nobody", store=user_store)
        guard.check()  # unknown user → unlimited


class TestBudgetGuardRecord:
    def test_accumulates_cost(self, user_store):
        from orchid.budget.guard import BudgetGuard
        guard = BudgetGuard("alice", store=user_store)
        guard.record(0.5)
        guard.record(0.3)
        user = user_store.get_user("alice")
        assert round(user.budget_used_usd, 8) == pytest.approx(0.8)

    def test_zero_cost_ignored(self, user_store):
        from orchid.budget.guard import BudgetGuard
        guard = BudgetGuard("alice", store=user_store)
        guard.record(0.0)
        user = user_store.get_user("alice")
        assert user.budget_used_usd == 0.0

    def test_unknown_user_no_error(self, user_store):
        from orchid.budget.guard import BudgetGuard
        guard = BudgetGuard("nobody", store=user_store)
        guard.record(1.0)  # must not raise


class TestBudgetGuardRemaining:
    def test_unlimited_returns_none(self, user_store):
        from orchid.budget.guard import BudgetGuard
        user = user_store.get_user("alice")
        user.budget_usd = 0.0
        user_store.update_user(user)
        assert BudgetGuard("alice", store=user_store).remaining() is None

    def test_partial_budget_used(self, user_store):
        from orchid.budget.guard import BudgetGuard
        user = user_store.get_user("alice")
        user.budget_used_usd = 3.0
        user_store.update_user(user)
        assert BudgetGuard("alice", store=user_store).remaining() == pytest.approx(7.0)

    def test_exhausted_returns_zero(self, user_store):
        from orchid.budget.guard import BudgetGuard
        user = user_store.get_user("alice")
        user.budget_used_usd = 12.0  # over limit
        user_store.update_user(user)
        assert BudgetGuard("alice", store=user_store).remaining() == 0.0


# ── vault_env_context ─────────────────────────────────────────────────────────

class TestVaultEnvContext:
    def test_no_vault_key_yields_empty(self, monkeypatch, tmp_path):
        """vault_env_context gracefully handles missing ORCHID_VAULT_KEY."""
        monkeypatch.delenv("ORCHID_VAULT_KEY", raising=False)
        # Reset vault singleton so fresh VaultStore is created
        import orchid.vault.store as vs
        vs._vault_instance = None
        from orchid.budget.guard import vault_env_context
        with vault_env_context("alice") as injected:
            assert injected == {}

    def test_injects_provider_vars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHID_VAULT_KEY", "test-master-key-for-vault-tests")
        import orchid.vault.store as vs
        vs._vault_instance = None
        vault = vs.VaultStore(users_dir=tmp_path)
        vault.set("alice", "ANTHROPIC_API_KEY", "sk-test-anthropic")
        vault.set("alice", "MY_CUSTOM_TOKEN", "custom-value")  # not in _PROVIDER_ENV_VARS

        from orchid.budget.guard import vault_env_context, get_env
        with vault_env_context("alice", vault_store=vault) as injected:
            assert "ANTHROPIC_API_KEY" in injected
            assert injected["ANTHROPIC_API_KEY"] == "sk-test-anthropic"
            # thread-local get_env should return vault value
            assert get_env("ANTHROPIC_API_KEY") == "sk-test-anthropic"
            # non-provider key must NOT be injected
            assert "MY_CUSTOM_TOKEN" not in injected

        # after context exit, thread-local is restored
        assert get_env("ANTHROPIC_API_KEY") != "sk-test-anthropic" or \
               os.environ.get("ANTHROPIC_API_KEY") == "sk-test-anthropic"

    def test_thread_local_isolation(self, tmp_path, monkeypatch):
        """Two threads get their own env overrides."""
        monkeypatch.setenv("ORCHID_VAULT_KEY", "test-master-key-isolation")
        import orchid.vault.store as vs
        vs._vault_instance = None
        vault = vs.VaultStore(users_dir=tmp_path)
        vault.set("user1", "ANTHROPIC_API_KEY", "key-for-user1")
        vault.set("user2", "ANTHROPIC_API_KEY", "key-for-user2")

        from orchid.budget.guard import vault_env_context, get_env

        results: dict[str, str | None] = {}
        errors: list[Exception] = []

        import time

        def thread_fn(uid: str, delay: float) -> None:
            try:
                with vault_env_context(uid, vault_store=vault):
                    time.sleep(delay)
                    results[uid] = get_env("ANTHROPIC_API_KEY")
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=thread_fn, args=("user1", 0.05))
        t2 = threading.Thread(target=thread_fn, args=("user2", 0.0))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, errors
        assert results.get("user1") == "key-for-user1"
        assert results.get("user2") == "key-for-user2"


# ── _compute_anthropic_cost ───────────────────────────────────────────────────

class TestComputeAnthropicCost:
    def test_sonnet4_cost(self):
        from orchid.budget.guard import _compute_anthropic_cost
        # 1M input + 1M output at $3/$15 per 1M
        cost = _compute_anthropic_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.0)

    def test_haiku4_cost(self):
        from orchid.budget.guard import _compute_anthropic_cost
        cost = _compute_anthropic_cost("claude-haiku-4-5-20251001", 100_000, 50_000)
        assert cost == pytest.approx(0.08 + 0.20, rel=1e-3)

    def test_unknown_model_returns_zero(self):
        from orchid.budget.guard import _compute_anthropic_cost
        cost = _compute_anthropic_cost("gpt-4o", 1_000_000, 1_000_000)
        assert cost == 0.0


# ── executor integration ──────────────────────────────────────────────────────

@pytest.fixture()
def executor_store(tmp_path):
    os.environ.setdefault("JWT_SECRET", "test-exec-budget")
    from orchid.auth.store import FileUserStore
    from orchid.auth.jwt import hash_password
    from orchid.auth.types import User
    import orchid.auth.store as store_mod

    store = FileUserStore(path=tmp_path / "users.json")
    alice = User(
        user_id="alice", username="alice", role="user", is_active=True,
        password_hash=hash_password("pw"),
        budget_usd=5.0, budget_used_usd=5.0,  # already exhausted
    )
    store.add_user(alice)
    store_mod._store_instance = store
    yield store
    store_mod._store_instance = None


class TestExecutorBudget:
    def test_budget_exceeded_run_fails(self, executor_store):
        """execute() returns failure run when budget is exhausted."""
        from orchid.cron.executor import TaskExecutor
        executor = TaskExecutor()
        task = {
            "task_id": "stask_1",
            "name": "test",
            "task_type": "shell",
            "config": {"command": "echo hi"},
        }
        run = executor.execute(task, owner_id="alice")
        assert run.status == "failure"
        assert "budget exceeded" in run.error.lower()

    def test_cost_recorded_after_success(self, tmp_path):
        """Cost accumulated in _exec_local.cost_usd is persisted after success."""
        os.environ.setdefault("JWT_SECRET", "test-cost-record")
        from orchid.auth.store import FileUserStore
        from orchid.auth.jwt import hash_password
        from orchid.auth.types import User
        import orchid.auth.store as store_mod
        import orchid.cron.executor as ex_mod

        store = FileUserStore(path=tmp_path / "users.json")
        bob = User(
            user_id="bob", username="bob", role="user", is_active=True,
            password_hash=hash_password("pw"),
            budget_usd=50.0, budget_used_usd=0.0,
        )
        store.add_user(bob)
        store_mod._store_instance = store

        from orchid.cron.executor import TaskExecutor, _exec_local

        # Patch _run_shell to inject fake cost
        def _fake_shell(config):
            _exec_local.cost_usd = getattr(_exec_local, "cost_usd", 0.0) + 0.0042
            return "ok"

        executor = TaskExecutor()
        original_dispatch = executor._DISPATCH.copy()
        executor._DISPATCH = {**original_dispatch, "shell": _fake_shell}

        try:
            task = {
                "task_id": "stask_2",
                "name": "test",
                "task_type": "shell",
                "config": {"command": "echo hi"},
            }
            run = executor.execute(task, owner_id="bob")
            assert run.status == "success"
            user = store.get_user("bob")
            assert user.budget_used_usd == pytest.approx(0.0042)
        finally:
            store_mod._store_instance = None


# ── Budget reset endpoint ─────────────────────────────────────────────────────

@pytest.fixture()
def reset_client(tmp_path):
    pytest.importorskip("fastapi")
    os.environ.setdefault("JWT_SECRET", "test-budget-reset")
    from fastapi.testclient import TestClient
    import orchid.interfaces.web_server as ws
    from orchid.auth.store import FileUserStore
    from orchid.auth.audit import AuditStore
    from orchid.auth.jwt import hash_password
    from orchid.auth.types import User
    import orchid.auth.store as store_mod

    ws._projects.clear(); ws._managers.clear(); ws._runners.clear()
    ws._main_loop = None; ws._auth_store = None; ws._audit_store = None

    new_store = FileUserStore(path=tmp_path / "users.json")
    new_audit = AuditStore(audit_dir=tmp_path / "audit")
    ws._auth_store = new_store
    ws._audit_store = new_audit
    store_mod._store_instance = new_store

    admin = User(
        user_id="admin1", username="admin", role="admin",
        is_active=True, password_hash=hash_password("adminpass"),
    )
    alice = User(
        user_id="user1", username="alice", role="user",
        is_active=True, password_hash=hash_password("pw"),
        budget_usd=10.0, budget_used_usd=7.5,
    )
    new_store.add_user(admin)
    new_store.add_user(alice)

    app = ws.create_app([])
    client = TestClient(app, raise_server_exceptions=True)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "adminpass"})
    assert r.status_code == 200, r.text
    yield client, new_store


class TestBudgetResetEndpoint:
    def test_reset_clears_used_usd(self, reset_client):
        client, store = reset_client
        r = client.post("/api/admin/users/user1/budget/reset")
        assert r.status_code == 200
        assert r.json()["budget_used_usd"] == 0.0
        assert store.get_user("user1").budget_used_usd == 0.0

    def test_reset_unknown_user_404(self, reset_client):
        client, _ = reset_client
        r = client.post("/api/admin/users/nobody/budget/reset")
        assert r.status_code == 404

    def test_budget_used_usd_in_user_list(self, reset_client):
        client, _ = reset_client
        r = client.get("/api/auth/users")
        assert r.status_code == 200
        alice = next(u for u in r.json()["users"] if u["username"] == "alice")
        assert "budget_used_usd" in alice
        assert alice["budget_used_usd"] == pytest.approx(7.5)
