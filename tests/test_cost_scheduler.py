"""Tests for orchid/cost/scheduler.py."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from orchid.cost.scheduler import (
    BudgetBlockedError,
    CostScheduler,
    RateLimitState,
    SpendSnapshot,
    ThrottleBlockedError,
    configure_cost_scheduler,
    get_cost_scheduler,
    reset_cost_scheduler,
)


def _make_scheduler(**kwargs) -> CostScheduler:
    """Create a CostScheduler with optional config overrides."""
    return CostScheduler(**kwargs)


def _patch_cost_config(**overrides):
    """Return a context manager that patches cfg.get with the given overrides.

    Pass overrides as keyword args with underscores instead of dots:
        _patch_cost_config(cost_budget_usd=10.0)
    maps to cfg.get("cost.budget_usd", 10.0).
    """
    defaults = {
        "cost.budget_usd": None,
        "cost.budget_warn_pct": 0.8,
        "cost.budget_stop": True,
    }
    for key, value in overrides.items():
        # Replace only the first underscore: cost_budget_usd -> cost.budget_usd
        parts = key.split("_", 1)
        dotted = parts[0] + "." + parts[1]
        defaults[dotted] = value
    side_effect = lambda k, default=None: defaults.get(k, default)
    return patch("orchid.cost.scheduler.cfg", get=side_effect)


# ── SpendSnapshot tests ──────────────────────────────────────────────────────

def test_snapshot_defaults():
    snap = SpendSnapshot()
    assert snap.total_cost_usd == 0.0
    assert snap.total_input_tokens == 0
    assert snap.total_output_tokens == 0
    assert snap.total_tokens == 0
    assert snap.per_model == {}
    assert snap.per_provider == {}
    assert snap.timestamp is not None


def test_snapshot_budget_remaining_no_cap():
    snap = SpendSnapshot(total_cost_usd=10.0)
    with _patch_cost_config(cost_budget_usd=None):
        assert snap.budget_remaining_usd == float("inf")


def test_snapshot_budget_remaining_with_cap():
    snap = SpendSnapshot(total_cost_usd=3.5)
    with _patch_cost_config(cost_budget_usd=10.0):
        assert snap.budget_remaining_usd == 6.5


def test_snapshot_budget_remaining_capped_at_zero():
    snap = SpendSnapshot(total_cost_usd=15.0)
    with _patch_cost_config(cost_budget_usd=10.0):
        assert snap.budget_remaining_usd == 0.0


def test_snapshot_budget_warn_pct_no_cap():
    snap = SpendSnapshot(total_cost_usd=999.0)
    with _patch_cost_config(cost_budget_usd=None):
        assert snap.budget_warn_pct == 0.0


def test_snapshot_budget_warn_pct_with_cap():
    snap = SpendSnapshot(total_cost_usd=8.0)
    with _patch_cost_config(cost_budget_usd=10.0):
        assert snap.budget_warn_pct == 0.8


def test_snapshot_budget_warn_triggered():
    snap = SpendSnapshot(total_cost_usd=8.5)
    with _patch_cost_config(cost_budget_usd=10.0, cost_budget_warn_pct=0.8):
        assert snap.budget_warn_triggered is True


def test_snapshot_budget_warn_not_triggered():
    snap = SpendSnapshot(total_cost_usd=7.0)
    with _patch_cost_config(cost_budget_usd=10.0, cost_budget_warn_pct=0.8):
        assert snap.budget_warn_triggered is False


def test_snapshot_budget_exceeded():
    snap = SpendSnapshot(total_cost_usd=11.0)
    with _patch_cost_config(cost_budget_usd=10.0):
        assert snap.budget_exceeded is True


def test_snapshot_budget_not_exceeded():
    snap = SpendSnapshot(total_cost_usd=9.0)
    with _patch_cost_config(cost_budget_usd=10.0):
        assert snap.budget_exceeded is False


def test_snapshot_budget_not_exceeded_no_cap():
    snap = SpendSnapshot(total_cost_usd=9999.0)
    with _patch_cost_config(cost_budget_usd=None):
        assert snap.budget_exceeded is False


# ── RateLimitState tests ─────────────────────────────────────────────────────

def test_rate_limit_defaults():
    state = RateLimitState()
    assert state.consecutive_429 == 0
    assert state.last_429_at == 0.0
    assert state.backoff_until == 0.0
    assert state.throttle_active is False


def test_rate_limit_not_throttled_by_default():
    state = RateLimitState()
    assert state.is_throttled is False


def test_rate_limit_backoff_remaining_zero_when_not_throttled():
    state = RateLimitState()
    assert state.backoff_remaining == 0.0


def test_rate_limit_is_throttled_when_active():
    state = RateLimitState(throttle_active=True, backoff_until=time.monotonic() + 10.0)
    assert state.is_throttled is True


def test_rate_limit_not_throttled_when_past_backoff():
    state = RateLimitState(throttle_active=True, backoff_until=time.monotonic() - 1.0)
    assert state.is_throttled is False


def test_rate_limit_backoff_remaining_positive():
    future = time.monotonic() + 5.0
    state = RateLimitState(throttle_active=True, backoff_until=future)
    assert state.backoff_remaining > 0.0


def test_rate_limit_backoff_remaining_zero_when_past():
    past = time.monotonic() - 5.0
    state = RateLimitState(throttle_active=True, backoff_until=past)
    assert state.backoff_remaining == 0.0


# ── CostScheduler: budget checks ─────────────────────────────────────────────

def test_check_budget_no_cap_passes():
    scheduler = _make_scheduler()
    scheduler.check_budget()


def test_check_budget_within_cap_passes():
    scheduler = _make_scheduler()
    with _patch_cost_config(cost_budget_usd=100.0):
        scheduler.check_budget()


def test_check_budget_exceeded_raises():
    scheduler = _make_scheduler()
    with _patch_cost_config(cost_budget_usd=10.0):
        scheduler._snapshot.total_cost_usd = 15.0
        with pytest.raises(BudgetBlockedError) as exc_info:
            scheduler.check_budget()
        assert "Budget cap" in str(exc_info.value)
        assert "exceeded" in str(exc_info.value).lower()


def test_check_budget_stop_false_passes_even_when_exceeded():
    scheduler = _make_scheduler()
    # _budget_stop is set at __init__ time from config, so we set it directly
    scheduler._budget_stop = False
    with _patch_cost_config(cost_budget_usd=10.0):
        scheduler._snapshot.total_cost_usd = 15.0
        scheduler.check_budget()  # should not raise


def test_check_budget_at_exact_cap_does_not_raise():
    scheduler = _make_scheduler()
    with _patch_cost_config(cost_budget_usd=10.0):
        scheduler._snapshot.total_cost_usd = 10.0
        scheduler.check_budget()


def test_check_budget_over_cap_raises():
    scheduler = _make_scheduler()
    with _patch_cost_config(cost_budget_usd=10.0):
        scheduler._snapshot.total_cost_usd = 10.0001
        with pytest.raises(BudgetBlockedError):
            scheduler.check_budget()


# ── CostScheduler: rate limit checks ─────────────────────────────────────────

def test_check_rate_limit_no_throttle_passes():
    scheduler = _make_scheduler()
    scheduler.check_rate_limit()


def test_check_rate_limit_throttled_raises():
    scheduler = _make_scheduler()
    scheduler._rate_limit.throttle_active = True
    scheduler._rate_limit.backoff_until = time.monotonic() + 10.0
    with pytest.raises(ThrottleBlockedError) as exc_info:
        scheduler.check_rate_limit()
    assert "back-off" in str(exc_info.value).lower()


def test_check_rate_limit_throttle_expired_passes():
    scheduler = _make_scheduler()
    scheduler._rate_limit.throttle_active = True
    scheduler._rate_limit.backoff_until = time.monotonic() - 1.0
    scheduler.check_rate_limit()


# ── CostScheduler: 429 recording ─────────────────────────────────────────────

def test_record_429_first():
    scheduler = _make_scheduler()
    scheduler.record_429()
    assert scheduler._rate_limit.consecutive_429 == 1
    assert scheduler._rate_limit.throttle_active is True
    assert scheduler._rate_limit.backoff_until > time.monotonic()


def test_record_429_increments_counter():
    scheduler = _make_scheduler()
    scheduler.record_429()
    scheduler.record_429()
    scheduler.record_429()
    assert scheduler._rate_limit.consecutive_429 == 3


def test_record_429_backoff_increases():
    scheduler = _make_scheduler()
    scheduler.record_429()
    first_backoff = scheduler._rate_limit.backoff_until
    scheduler.record_429()
    second_backoff = scheduler._rate_limit.backoff_until
    assert second_backoff > first_backoff


def test_record_429_backoff_capped_at_60s():
    scheduler = _make_scheduler()
    for _ in range(10):
        scheduler.record_429()
    assert scheduler._rate_limit.consecutive_429 == 10
    assert scheduler._rate_limit.backoff_until >= time.monotonic() + 60


def test_reset_429_clears_state():
    scheduler = _make_scheduler()
    scheduler.record_429()
    assert scheduler._rate_limit.consecutive_429 == 1
    scheduler.reset_429()
    assert scheduler._rate_limit.consecutive_429 == 0
    assert scheduler._rate_limit.throttle_active is False
    assert scheduler._rate_limit.backoff_until == 0.0


def test_reset_429_noop_when_already_zero():
    scheduler = _make_scheduler()
    scheduler.reset_429()
    assert scheduler._rate_limit.consecutive_429 == 0


# ── CostScheduler: provider selection ────────────────────────────────────────

def test_select_cheapest_single_provider():
    scheduler = _make_scheduler()
    result = scheduler.select_cheapest_provider(["openai"], "code")
    assert result == "openai"


def test_select_cheapest_empty_list():
    scheduler = _make_scheduler()
    result = scheduler.select_cheapest_provider([], "code")
    assert result is None


def test_select_cheapest_no_budget_tight_returns_first():
    scheduler = _make_scheduler()
    result = scheduler.select_cheapest_provider(["anthropic", "openai", "local"], "code")
    assert result == "anthropic"


def test_select_cheapest_budget_tight_prefers_local():
    scheduler = _make_scheduler()
    scheduler._snapshot.total_cost_usd = 5.0
    with _patch_cost_config(cost_budget_usd=10.0):
        result = scheduler.select_cheapest_provider(
            ["anthropic", "openai", "local"], "code"
        )
        assert result == "local"


def test_select_cheapest_budget_tight_no_local():
    scheduler = _make_scheduler()
    scheduler._snapshot.total_cost_usd = 5.0
    scheduler._snapshot.per_model["anthropic"] = 0.1
    scheduler._snapshot.per_model["openai"] = 0.5
    with _patch_cost_config(cost_budget_usd=10.0):
        result = scheduler.select_cheapest_provider(
            ["anthropic", "openai"], "code"
        )
        assert result == "anthropic"


def test_select_cheapest_budget_tight_equal_spend():
    scheduler = _make_scheduler()
    scheduler._snapshot.total_cost_usd = 5.0
    scheduler._snapshot.per_model["anthropic"] = 0.5
    scheduler._snapshot.per_model["openai"] = 0.5
    with _patch_cost_config(cost_budget_usd=10.0):
        result = scheduler.select_cheapest_provider(
            ["anthropic", "openai"], "code"
        )
        assert result in ("anthropic", "openai")


# ── CostScheduler: cost recording ────────────────────────────────────────────

def test_record_cost_updates_snapshot():
    scheduler = _make_scheduler()
    snap = scheduler.record_cost(
        task_id="T001", title="Test", model="gpt-4", provider="openai",
        input_tokens=1000, output_tokens=500, cost_usd=0.003,
    )
    assert snap.total_cost_usd == pytest.approx(0.003)
    assert snap.total_input_tokens == 1000
    assert snap.total_output_tokens == 500
    assert snap.total_tokens == 1500


def test_record_cost_accumulates():
    scheduler = _make_scheduler()
    scheduler.record_cost(
        task_id="T001", title="First", model="gpt-4", provider="openai",
        input_tokens=1000, output_tokens=500, cost_usd=0.003,
    )
    snap = scheduler.record_cost(
        task_id="T002", title="Second", model="gpt-4", provider="openai",
        input_tokens=2000, output_tokens=1000, cost_usd=0.006,
    )
    assert snap.total_cost_usd == pytest.approx(0.009)
    assert snap.total_input_tokens == 3000
    assert snap.total_output_tokens == 1500
    assert snap.total_tokens == 4500


def test_record_cost_per_model():
    scheduler = _make_scheduler()
    scheduler.record_cost(
        task_id="T001", title="T1", model="gpt-4", provider="openai",
        cost_usd=0.003,
    )
    scheduler.record_cost(
        task_id="T002", title="T2", model="claude-sonnet-4-20250514", provider="anthropic",
        cost_usd=0.006,
    )
    snap = scheduler.get_snapshot()
    assert snap.per_model["gpt-4"] == pytest.approx(0.003)
    assert snap.per_model["claude-sonnet-4-20250514"] == pytest.approx(0.006)


def test_record_cost_per_provider():
    scheduler = _make_scheduler()
    scheduler.record_cost(
        task_id="T001", title="T1", model="gpt-4", provider="openai",
        cost_usd=0.003,
    )
    scheduler.record_cost(
        task_id="T002", title="T2", model="gpt-4", provider="openai",
        cost_usd=0.006,
    )
    snap = scheduler.get_snapshot()
    assert snap.per_provider["openai"] == pytest.approx(0.009)


def test_record_cost_zero_values():
    scheduler = _make_scheduler()
    snap = scheduler.record_cost(
        task_id="T001", title="Zero", model="gpt-4", provider="openai",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
    )
    assert snap.total_cost_usd == 0.0
    assert snap.total_tokens == 0


def test_record_cost_returns_new_snapshot():
    scheduler = _make_scheduler()
    snap1 = scheduler.record_cost(
        task_id="T001", title="T1", model="gpt-4", provider="openai",
        cost_usd=0.003,
    )
    snap2 = scheduler.record_cost(
        task_id="T002", title="T2", model="gpt-4", provider="openai",
        cost_usd=0.006,
    )
    assert snap1.total_cost_usd == pytest.approx(0.003)
    assert snap2.total_cost_usd == pytest.approx(0.009)
    assert snap1 is not snap2


def test_record_cost_with_ledger_mock(tmp_path):
    project_dir = tmp_path / "ledger_test"
    project_dir.mkdir(parents=True)
    scheduler = CostScheduler(str(project_dir))
    with patch("orchid.cost.ledger.get_cost_ledger") as mock_ledger:
        mock_instance = mock_ledger.return_value
        scheduler.record_cost(
            task_id="T001", title="T1", model="gpt-4", provider="openai",
            input_tokens=100, output_tokens=50,
            prompt_tokens=100, cache_creation_input_tokens=0,
            cache_read_input_tokens=0, completion_tokens=50, cost_usd=0.001,
        )
        mock_instance.record.assert_called_once_with(
            task_id="T001", title="T1", model="gpt-4", provider="openai",
            input_tokens=100, output_tokens=50,
            prompt_tokens=100, cache_creation_input_tokens=0,
            cache_read_input_tokens=0, completion_tokens=50, cost_usd=0.001,
        )


def test_record_cost_ledger_failure_does_not_crash():
    scheduler = _make_scheduler()
    with patch("orchid.cost.ledger.get_cost_ledger") as mock_ledger:
        mock_ledger.side_effect = Exception("ledger broken")
        snap = scheduler.record_cost(
            task_id="T001", title="T1", model="gpt-4", provider="openai",
            cost_usd=0.001,
        )
        assert snap.total_cost_usd == pytest.approx(0.001)


def test_record_cost_budget_warning_once():
    scheduler = _make_scheduler()
    with _patch_cost_config(cost_budget_usd=10.0, cost_budget_warn_pct=0.8):
        scheduler.record_cost(
            task_id="T001", title="T1", model="gpt-4", provider="openai",
            cost_usd=8.0,
        )
        assert scheduler._warned_budget is True
        scheduler.record_cost(
            task_id="T002", title="T2", model="gpt-4", provider="openai",
            cost_usd=2.0,
        )
        assert scheduler._warned_budget is True


def test_record_cost_no_warning_below_threshold():
    scheduler = _make_scheduler()
    with _patch_cost_config(cost_budget_usd=10.0, cost_budget_warn_pct=0.8):
        scheduler.record_cost(
            task_id="T001", title="T1", model="gpt-4", provider="openai",
            cost_usd=5.0,
        )
        assert scheduler._warned_budget is False


# ── CostScheduler: status / introspection ────────────────────────────────────

def test_get_snapshot_returns_copy():
    scheduler = _make_scheduler()
    scheduler.record_cost(
        task_id="T001", title="T1", model="gpt-4", provider="openai",
        cost_usd=0.003,
    )
    snap1 = scheduler.get_snapshot()
    snap2 = scheduler.get_snapshot()
    assert snap1.total_cost_usd == snap2.total_cost_usd
    assert snap1 is not snap2


def test_get_snapshot_empty():
    scheduler = _make_scheduler()
    snap = scheduler.get_snapshot()
    assert snap.total_cost_usd == 0.0
    assert snap.total_tokens == 0


def test_get_rate_limit_state_returns_copy():
    scheduler = _make_scheduler()
    scheduler.record_429()
    state1 = scheduler.get_rate_limit_state()
    state2 = scheduler.get_rate_limit_state()
    assert state1.consecutive_429 == state2.consecutive_429
    assert state1 is not state2


def test_summary_empty():
    scheduler = _make_scheduler()
    summary = scheduler.summary()
    assert summary["total_cost_usd"] == 0.0
    assert summary["total_input_tokens"] == 0


def test_summary_with_data():
    scheduler = _make_scheduler()
    scheduler.record_cost(
        task_id="T001", title="T1", model="gpt-4", provider="openai",
        input_tokens=1000, output_tokens=500, cost_usd=0.003,
    )
    summary = scheduler.summary()
    assert summary["total_cost_usd"] == pytest.approx(0.003)
    assert summary["total_input_tokens"] == 1000
    assert summary["total_output_tokens"] == 500
    assert summary["total_tokens"] == 1500
    assert "gpt-4" in summary["per_model"]
    assert "openai" in summary["per_provider"]


def test_summary_no_budget():
    scheduler = _make_scheduler()
    summary = scheduler.summary()
    assert summary["budget_usd"] is None
    assert summary["budget_remaining_usd"] is None


def test_summary_with_budget():
    scheduler = _make_scheduler()
    scheduler.record_cost(
        task_id="T001", title="T1", model="gpt-4", provider="openai",
        cost_usd=3.5,
    )
    with _patch_cost_config(cost_budget_usd=10.0):
        summary = scheduler.summary()
        assert summary["budget_usd"] == 10.0
        assert summary["budget_remaining_usd"] == 6.5
        assert summary["budget_warn_triggered"] is False
        assert summary["budget_exceeded"] is False


def test_summary_rate_limit():
    scheduler = _make_scheduler()
    scheduler.record_429()
    summary = scheduler.summary()
    assert summary["rate_limit"]["consecutive_429"] == 1
    assert summary["rate_limit"]["throttle_active"] is True


def test_summary_timestamp():
    scheduler = _make_scheduler()
    summary = scheduler.summary()
    assert summary["timestamp"] is not None


# ── CostScheduler: reset ─────────────────────────────────────────────────────

def test_reset_clears_all_state():
    scheduler = _make_scheduler()
    scheduler.record_cost(
        task_id="T001", title="T1", model="gpt-4", provider="openai",
        cost_usd=5.0,
    )
    scheduler.record_429()
    scheduler.reset()
    snap = scheduler.get_snapshot()
    assert snap.total_cost_usd == 0.0
    assert snap.total_tokens == 0
    state = scheduler.get_rate_limit_state()
    assert state.consecutive_429 == 0
    assert state.throttle_active is False


# ── CostScheduler: thread safety ─────────────────────────────────────────────

def test_record_cost_thread_safe():
    scheduler = _make_scheduler()
    num_tasks = 20
    results: list = []
    lock = threading.Lock()

    def worker(i: int):
        try:
            snap = scheduler.record_cost(
                task_id=f"T{i:03d}", title=f"Task {i}", model="gpt-4",
                provider="openai", cost_usd=0.001,
            )
            with lock:
                results.append(snap.total_cost_usd)
        except Exception as e:
            with lock:
                results.append(f"ERROR:{e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_tasks)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == num_tasks
    assert all(isinstance(r, float) for r in results)


def test_check_budget_thread_safe():
    scheduler = _make_scheduler()
    with _patch_cost_config(cost_budget_usd=0.005):
        scheduler.record_cost(
            task_id="T001", title="T1", model="gpt-4", provider="openai",
            cost_usd=0.003,
        )
        errors: list = []
        lock = threading.Lock()

        def checker():
            try:
                scheduler.check_budget()
            except BudgetBlockedError:
                pass
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=checker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0


# ── Module-level singleton functions ─────────────────────────────────────────

def test_get_cost_scheduler_creates_singleton():
    reset_cost_scheduler()
    scheduler = get_cost_scheduler()
    assert scheduler is not None
    assert isinstance(scheduler, CostScheduler)
    reset_cost_scheduler()


def test_get_cost_scheduler_returns_same_instance():
    reset_cost_scheduler()
    s1 = get_cost_scheduler()
    s2 = get_cost_scheduler()
    assert s1 is s2
    reset_cost_scheduler()


def test_get_cost_scheduler_with_project_dir():
    reset_cost_scheduler()
    scheduler = get_cost_scheduler("/tmp/test_project")
    assert scheduler._project_dir == "/tmp/test_project"
    reset_cost_scheduler()


def test_configure_cost_scheduler_replaces_singleton():
    reset_cost_scheduler()
    s1 = configure_cost_scheduler("/tmp/p1")
    s2 = configure_cost_scheduler("/tmp/p2")
    assert s1 is not s2
    assert s1._project_dir == "/tmp/p1"
    assert s2._project_dir == "/tmp/p2"
    reset_cost_scheduler()


def test_reset_cost_scheduler_clears_singleton():
    reset_cost_scheduler()
    s = get_cost_scheduler()
    assert s is not None
    reset_cost_scheduler()
    s2 = get_cost_scheduler()
    assert s2 is not s
    reset_cost_scheduler()


def test_reset_cost_scheduler_is_safe_when_none():
    reset_cost_scheduler()
    reset_cost_scheduler()


def test_configure_cost_scheduler_returns_scheduler():
    reset_cost_scheduler()
    s = configure_cost_scheduler()
    assert isinstance(s, CostScheduler)
    reset_cost_scheduler()


# ── Spec-required tests (T206) ────────────────────────────────────────────────

def test_select_provider_returns_first_candidate_by_default(tmp_path):
    from unittest.mock import patch
    from orchid.cost.scheduler import CostAwareScheduler, _rate_flags
    _rate_flags.clear()
    sched = CostAwareScheduler(str(tmp_path))
    with patch("orchid.cost.scheduler.cfg.get", return_value=None):
        result = sched.select_provider(["anthropic", "local"])
    assert result == "anthropic"


def test_select_provider_skips_over_budget(tmp_path):
    from unittest.mock import patch, MagicMock
    from orchid.cost.scheduler import CostAwareScheduler, _rate_flags
    _rate_flags.clear()
    sched = CostAwareScheduler(str(tmp_path))

    def cfg_side_effect(key, default=None):
        if "daily_budget_usd.anthropic" in key:
            return 1.0
        return default

    with patch("orchid.cost.scheduler.cfg.get", side_effect=cfg_side_effect):
        snap = MagicMock()
        snap.total_cost_usd = 1.5
        with patch.object(sched, "get_snapshot", return_value=snap):
            result = sched.select_provider(["anthropic", "local"])
    assert result == "local"


def test_select_provider_skips_rate_limited(tmp_path):
    from unittest.mock import patch
    from orchid.cost.scheduler import CostAwareScheduler, _rate_flags, set_rate_pressure
    _rate_flags.clear()
    sched = CostAwareScheduler(str(tmp_path))
    set_rate_pressure("anthropic", True)
    try:
        with patch("orchid.cost.scheduler.cfg.get", return_value=None):
            result = sched.select_provider(["anthropic", "local"])
        assert result == "local"
        set_rate_pressure("anthropic", False)
        with patch("orchid.cost.scheduler.cfg.get", return_value=None):
            result2 = sched.select_provider(["anthropic", "local"])
        assert result2 == "anthropic"
    finally:
        _rate_flags.clear()


def test_select_provider_fallback_when_all_fail(tmp_path):
    from unittest.mock import patch, MagicMock
    from orchid.cost.scheduler import CostAwareScheduler, _rate_flags, set_rate_pressure
    _rate_flags.clear()
    sched = CostAwareScheduler(str(tmp_path))
    set_rate_pressure("anthropic", True)
    set_rate_pressure("local", True)
    try:
        with patch("orchid.cost.scheduler.cfg.get", return_value=None):
            result = sched.select_provider(["anthropic", "local"])
        assert result == "local"
    finally:
        _rate_flags.clear()
