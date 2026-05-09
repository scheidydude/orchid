"""tests/test_user_quota.py — Tests for per-user daily spend tracking."""

from __future__ import annotations

from datetime import datetime, UTC, timedelta
from pathlib import Path

import pytest

from orchid.cost.ledger import CostLedger, TokenRecord


def _today_str() -> str:
    """Return today's UTC date string in ISO format (YYYY-MM-DD)."""
    return datetime.now(UTC).date().isoformat()


def test_daily_spend_for_user_sums_correctly(tmp_path: Path) -> None:
    """Create CostLedger, record three TokenRecords (two for alice, one for bob),
    and assert that daily_spend_for_user returns the correct sums."""
    ledger = CostLedger(tmp_path)

    # Record two entries for alice, each costing $1.00
    ledger.record(
        task_id="T001",
        title="Test task 1",
        model="gpt-4o",
        provider="openai",
        input_tokens=100,
        output_tokens=50,
        cost_usd=1.0,
        user_id="alice",
    )
    ledger.record(
        task_id="T002",
        title="Test task 2",
        model="claude-3.5",
        provider="anthropic",
        input_tokens=200,
        output_tokens=100,
        cost_usd=1.0,
        user_id="alice",
    )

    # Record one entry for bob costing $5.00
    ledger.record(
        task_id="T003",
        title="Test task 3",
        model="gpt-4o",
        provider="openai",
        input_tokens=500,
        output_tokens=250,
        cost_usd=5.0,
        user_id="bob",
    )

    assert ledger.daily_spend_for_user("alice") == 2.0
    assert ledger.daily_spend_for_user("bob") == 5.0


def test_daily_spend_for_user_empty_result(tmp_path: Path) -> None:
    """When no records exist for a user, daily_spend_for_user returns 0.0."""
    ledger = CostLedger(tmp_path)

    ledger.record(
        task_id="T001",
        title="Test task",
        model="gpt-4o",
        provider="openai",
        input_tokens=100,
        output_tokens=50,
        cost_usd=1.0,
        user_id="alice",
    )

    assert ledger.daily_spend_for_user("charlie") == 0.0


def test_daily_spend_for_user_ignores_previous_days(tmp_path: Path) -> None:
    """Records with timestamps from a previous day are excluded from the sum."""
    ledger = CostLedger(tmp_path)

    # Record an entry with yesterday's timestamp
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    past_record = TokenRecord(
        task_id="T000",
        title="Past task",
        model="gpt-4o",
        provider="openai",
        input_tokens=100,
        output_tokens=50,
        cost_usd=10.0,
        user_id="alice",
        timestamp=yesterday,
    )

    # Record today's entry
    ledger.record(
        task_id="T001",
        title="Today task",
        model="gpt-4o",
        provider="openai",
        input_tokens=100,
        output_tokens=50,
        cost_usd=2.0,
        user_id="alice",
    )

    # Manually inject the past record into the in-memory list
    # so _load doesn't re-read from disk (which would overwrite)
    ledger._records.insert(0, past_record)

    # Only today's record ($2.00) should be counted
    assert ledger.daily_spend_for_user("alice") == 2.0