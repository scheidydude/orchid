"""Tests for CPU/latency budgets — Phase 6."""

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from orchid.agents.base import AgentCancelledError, BaseAgent
from orchid.cost.ledger import CostLedger, TokenRecord
from orchid.cost.scheduler import CostScheduler
from orchid.worker_protocol import WorkerResult


# ── WorkerResult cpu_seconds ──────────────────────────────────────────────────

class TestWorkerResultCpuSeconds:
    def test_default_is_zero(self):
        r = WorkerResult(task_id="T001", success=True)
        assert r.cpu_seconds == 0.0

    def test_field_survives_json_roundtrip(self):
        r = WorkerResult(task_id="T001", success=True, cpu_seconds=4.2)
        parsed = json.loads(r.to_json())
        assert parsed["cpu_seconds"] == pytest.approx(4.2)

    def test_old_json_without_field_safe(self):
        """WorkerResult deserialized from JSON lacking cpu_seconds must not crash."""
        old_json = json.dumps({"task_id": "T1", "success": True, "result": "", "error": "",
                               "duration_s": 0.0})
        data = json.loads(old_json)
        r = WorkerResult(**{k: v for k, v in data.items()
                            if k in WorkerResult.__dataclass_fields__})
        assert r.cpu_seconds == 0.0


# ── TokenRecord cpu_seconds ───────────────────────────────────────────────────

class TestTokenRecordCpuSeconds:
    def test_default_is_zero(self):
        r = TokenRecord(task_id="T1", title="t", model="m", provider="p")
        assert r.cpu_seconds == 0.0

    def test_cpu_seconds_in_to_dict(self):
        r = TokenRecord(task_id="T1", title="t", model="m", provider="p", cpu_seconds=7.5)
        assert r.to_dict()["cpu_seconds"] == pytest.approx(7.5)


# ── CostLedger daily_cpu_for_user ─────────────────────────────────────────────

class TestDailyCpuForUser:
    def test_zero_when_no_records(self, tmp_path):
        ledger = CostLedger(tmp_path)
        assert ledger.daily_cpu_for_user("alice") == 0.0

    def test_sums_todays_cpu_for_user(self, tmp_path):
        ledger = CostLedger(tmp_path)
        ledger.record("T1", "t1", "claude", "anthropic", cpu_seconds=10.0, user_id="alice")
        ledger.record("T2", "t2", "claude", "anthropic", cpu_seconds=5.0, user_id="alice")
        ledger.record("T3", "t3", "claude", "anthropic", cpu_seconds=3.0, user_id="bob")
        assert ledger.daily_cpu_for_user("alice") == pytest.approx(15.0)
        assert ledger.daily_cpu_for_user("bob") == pytest.approx(3.0)

    def test_excludes_yesterday(self, tmp_path):
        """Records with yesterday's date don't count."""
        ledger = CostLedger(tmp_path)
        old_record = TokenRecord(
            task_id="TOLD", title="t", model="m", provider="p",
            cpu_seconds=100.0, user_id="alice",
        )
        d = dataclasses.asdict(old_record)
        d["timestamp"] = "2020-01-01T00:00:00+00:00"
        ledger._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger._ledger_path, "a") as f:
            f.write(json.dumps(d) + "\n")
        ledger._load()
        assert ledger.daily_cpu_for_user("alice") == 0.0


# ── CostScheduler.check_cpu_budget ───────────────────────────────────────────

class TestCheckCpuBudget:
    def test_zero_budget_never_raises(self, tmp_path):
        sched = CostScheduler(project_dir=tmp_path)
        sched.check_cpu_budget("alice", 0.0)  # must not raise

    def test_under_budget_does_not_raise(self, tmp_path):
        ledger = CostLedger(tmp_path)
        ledger.record("T1", "t", "m", "p", cpu_seconds=50.0, user_id="alice")
        sched = CostScheduler(project_dir=tmp_path)
        sched.check_cpu_budget("alice", 3600.0)  # 50s used, 3600s limit

    def test_over_budget_raises(self, tmp_path):
        from orchid.cost.scheduler import BudgetBlockedError
        ledger = CostLedger(tmp_path)
        ledger.record("T1", "t", "m", "p", cpu_seconds=3700.0, user_id="alice")
        sched = CostScheduler(project_dir=tmp_path)
        with pytest.raises(BudgetBlockedError, match="CPU quota"):
            sched.check_cpu_budget("alice", 3600.0)


# ── Per-iteration latency tracking ───────────────────────────────────────────

class TestIterationLatencyBudget:
    def _make_agent_with_cfg(self):
        """Agent that uses max_iteration_seconds=120 from mocked config."""
        agent = BaseAgent()
        agent.max_iterations = 10
        return agent

    def test_no_budget_configured_completes_normally(self):
        """When max_iteration_seconds=0 (disabled), agent runs to completion."""
        agent = BaseAgent()
        agent.max_iterations = 3

        with patch("orchid.agents.base.cfg") as mock_cfg:
            mock_cfg.get.side_effect = lambda key, default=None: (
                0 if key == "agents.max_iteration_seconds" else
                3 if key == "agents.max_react_iterations" else
                30 if key == "agents.tool_timeout_seconds" else
                False if key == "agents.verify_syntax_only" else
                default
            )
            with patch("orchid.agents.base.call", return_value="Thought: x\nFinal Answer: done"):
                result = agent.run("task")
        assert result == "done"

    def test_three_consecutive_slow_iters_cancel(self):
        """AgentCancelledError raised after 3 consecutive slow iterations."""
        agent = BaseAgent()
        agent.max_iterations = 10
        agent.tools["read_file"] = lambda path: "content"

        responses = iter([
            "Thought: x\nAction: read_file\nAction Input: {\"path\": \"f\"}",
            "Thought: x\nAction: read_file\nAction Input: {\"path\": \"f\"}",
            "Thought: x\nAction: read_file\nAction Input: {\"path\": \"f\"}",
            "Thought: done\nFinal Answer: ok",
        ])
        # Each pair (start, end) of time.monotonic() calls per iteration
        # iteration boundary check + call() start + call() end
        # Exactly 2 values per iteration: _iter_start and the second time.monotonic() call
        monotonic_values = iter([
            0.0, 200.0,    # iter 0: elapsed=200.0 → slow (1/3)
            200.0, 400.0,  # iter 1: elapsed=200.0 → slow (2/3)
            400.0, 600.0,  # iter 2: elapsed=200.0 → slow (3/3) → raise
        ])

        with patch("orchid.agents.base.cfg") as mock_cfg:
            mock_cfg.get.side_effect = lambda key, default=None: (
                120.0 if key == "agents.max_iteration_seconds" else
                10 if key == "agents.max_react_iterations" else
                30 if key == "agents.tool_timeout_seconds" else
                False if key == "agents.verify_syntax_only" else
                default
            )
            with patch("orchid.agents.base.time") as mock_time:
                mock_time.monotonic.side_effect = lambda: next(monotonic_values, 9999.0)
                with patch("orchid.agents.base.call", side_effect=lambda **kw: next(responses)):
                    with pytest.raises(AgentCancelledError, match="latency budget"):
                        agent.run("task")

    def test_fast_iter_resets_slow_counter(self):
        """A fast iteration resets the consecutive-slow counter; 2+reset+2 ≠ cancel."""
        agent = BaseAgent()
        agent.max_iterations = 10
        agent.tools["read_file"] = lambda path: "content"

        # 2 slow, 1 fast (resets), 2 slow → total < 3 consecutive → should not cancel
        responses = iter([
            "Thought: x\nAction: read_file\nAction Input: {\"path\": \"f\"}",  # slow
            "Thought: x\nAction: read_file\nAction Input: {\"path\": \"f\"}",  # slow
            "Thought: x\nAction: read_file\nAction Input: {\"path\": \"f\"}",  # fast (reset)
            "Thought: x\nAction: read_file\nAction Input: {\"path\": \"f\"}",  # slow
            "Thought: x\nAction: read_file\nAction Input: {\"path\": \"f\"}",  # slow
            "Thought: done\nFinal Answer: ok",
        ])
        # monotonic: each iteration has (start, call_start, call_end) but
        # we just need _iter_elapsed = call_end - call_start
        t = [0.0]

        def monotonic():
            return t[0]

        call_durations = iter([200.0, 200.0, 1.0, 200.0, 200.0, 1.0])

        real_responses = list(responses)
        resp_iter = iter(real_responses)

        def fake_call(**kw):
            duration = next(call_durations, 1.0)
            t[0] += duration
            return next(resp_iter)

        with patch("orchid.agents.base.cfg") as mock_cfg:
            mock_cfg.get.side_effect = lambda key, default=None: (
                120.0 if key == "agents.max_iteration_seconds" else
                10 if key == "agents.max_react_iterations" else
                30 if key == "agents.tool_timeout_seconds" else
                False if key == "agents.verify_syntax_only" else
                default
            )
            with patch("orchid.agents.base.time") as mock_time:
                mock_time.monotonic.side_effect = monotonic
                with patch("orchid.agents.base.call", side_effect=fake_call):
                    result = agent.run("task")

        assert result == "ok"


# ── User.cpu_budget_seconds ───────────────────────────────────────────────────

class TestUserCpuBudgetField:
    def test_default_is_zero(self):
        from orchid.auth.types import User
        u = User(user_id="u1")
        assert u.cpu_budget_seconds == 0.0

    def test_roundtrip_through_file_store(self, tmp_path):
        from orchid.auth.types import User
        from orchid.auth.store import FileUserStore
        store = FileUserStore(path=tmp_path / "users.json")
        u = User(user_id="u1", username="alice", cpu_budget_seconds=7200.0)
        store.add_user(u)
        loaded = store.get_user("u1")
        assert loaded.cpu_budget_seconds == pytest.approx(7200.0)
