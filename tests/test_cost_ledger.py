"""Tests for orchid/cost/ledger.py."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from orchid.cost.ledger import (
    BudgetExceededError,
    CostLedger,
    CostLedgerError,
    TokenRecord,
    configure_cost_ledger,
    get_cost_ledger,
    reset_cost_ledger,
)


def _make_ledger(tmp_path: Path) -> CostLedger:
    project_dir = tmp_path / "test_project"
    project_dir.mkdir(parents=True)
    return CostLedger(project_dir)


def _make_record(**kwargs) -> TokenRecord:
    defaults = dict(
        task_id="T001", title="Test task",
        model="claude-sonnet-4-20250514", provider="anthropic",
        input_tokens=1000, output_tokens=500, prompt_tokens=1000,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
        completion_tokens=500, total_tokens=1500, cost_usd=0.003,
    )
    defaults.update(kwargs)
    return TokenRecord(**defaults)


def test_token_record_defaults():
    rec = TokenRecord(task_id="T001", title="Test", model="gpt-4", provider="openai")
    assert rec.input_tokens == 0
    assert rec.output_tokens == 0
    assert rec.prompt_tokens == 0
    assert rec.cache_creation_input_tokens == 0
    assert rec.cache_read_input_tokens == 0
    assert rec.completion_tokens == 0
    assert rec.total_tokens == 0
    assert rec.cost_usd == 0.0


def test_token_record_input_token_total():
    rec = _make_record(cache_creation_input_tokens=200, cache_read_input_tokens=300, input_tokens=1000, prompt_tokens=1000)
    assert rec.input_token_total == 500
    rec2 = _make_record(cache_creation_input_tokens=0, cache_read_input_tokens=0, input_tokens=1000, prompt_tokens=1000)
    assert rec2.input_token_total == 1000
    rec3 = _make_record(cache_creation_input_tokens=0, cache_read_input_tokens=0, input_tokens=0, prompt_tokens=800)
    assert rec3.input_token_total == 800


def test_token_record_output_token_total():
    rec = _make_record(completion_tokens=600, output_tokens=500)
    assert rec.output_token_total == 600
    rec2 = _make_record(completion_tokens=0, output_tokens=500)
    assert rec2.output_token_total == 500


def test_token_record_grand_total():
    # grand_total = input_token_total + output_token_total (NO double-counting cache)
    # input_token_total prefers cache tokens: 100+200=300
    # output_token_total prefers completion_tokens: 500
    # grand_total = 300 + 500 = 800
    rec = _make_record(cache_creation_input_tokens=100, cache_read_input_tokens=200, input_tokens=500, output_tokens=300, completion_tokens=500)
    assert rec.grand_total == 800


def test_token_record_to_dict_roundtrip():
    rec = _make_record(task_id="T005", title="Roundtrip test", input_tokens=1234, output_tokens=567, cost_usd=0.0123)
    data = rec.to_dict()
    rec2 = TokenRecord.from_dict(data)
    assert rec2.task_id == rec.task_id
    assert rec2.title == rec.title
    assert rec2.input_tokens == rec.input_tokens
    assert rec2.output_tokens == rec.output_tokens
    assert rec2.cost_usd == rec.cost_usd


def test_token_record_from_dict_ignores_unknown_fields():
    data = {
        "task_id": "T001", "title": "Test", "model": "gpt-4", "provider": "openai",
        "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
        "cost_usd": 0.001, "timestamp": "2025-01-01T00:00:00+00:00",
        "extra_unknown_field": "ignored",
    }
    rec = TokenRecord.from_dict(data)
    assert rec.task_id == "T001"
    assert rec.input_tokens == 100
    assert not hasattr(rec, "extra_unknown_field")


def test_ledger_creates_project_dir(tmp_path: Path):
    """CostLedger creates the .orchid directory on first record write."""
    ledger = _make_ledger(tmp_path)
    # .orchid dir does NOT exist yet — only created by _flush on first record
    assert not (tmp_path / "test_project" / ".orchid").exists()
    # After a record, the .orchid dir and file should exist
    ledger.record(task_id="T001", title="Test", model="gpt-4", provider="openai")
    assert (tmp_path / "test_project" / ".orchid").exists()
    assert ledger._ledger_path.exists()


def test_ledger_empty_on_new_project(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    assert ledger._records == []
    assert ledger.get_latest() is None


def test_record_adds_to_memory(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    rec = ledger.record(task_id="T001", title="First task", model="claude-sonnet-4-20250514",
                        provider="anthropic", input_tokens=1000, output_tokens=500, cost_usd=0.003)
    assert len(ledger._records) == 1
    assert rec.task_id == "T001"
    assert rec.cost_usd == 0.003


def test_record_persists_to_file(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="Persist test", model="gpt-4", provider="openai",
                  input_tokens=100, output_tokens=50, cost_usd=0.001)
    lines = ledger._ledger_path.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["task_id"] == "T001"
    assert data["cost_usd"] == 0.001


def test_record_multiple_appends(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    for i in range(5):
        ledger.record(task_id=f"T{i:03d}", title=f"Task {i}", model="gpt-4", provider="openai",
                      input_tokens=100, output_tokens=50, cost_usd=0.001)
    lines = ledger._ledger_path.read_text().strip().split("\n")
    assert len(lines) == 5
    assert len(ledger._records) == 5


def test_record_timestamp_is_isoformat(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    rec = ledger.record(task_id="T001", title="Timestamp test", model="gpt-4", provider="openai")
    from datetime import datetime
    dt = datetime.fromisoformat(rec.timestamp)
    assert dt.tzinfo is not None


def test_record_returns_the_record(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    rec = ledger.record(task_id="T001", title="Return test", model="gpt-4", provider="openai", input_tokens=100)
    assert rec.task_id == "T001"
    assert rec.input_tokens == 100


def test_record_exceeds_budget_raises(tmp_path: Path):
    with patch("orchid.cost.ledger.get") as mock_get:
        mock_get.side_effect = lambda key, default=None: {
            "cost.budget_usd": 0.01, "cost.budget_warn_pct": 0.8,
        }.get(key, default)
        ledger = CostLedger(tmp_path / "budget_test")
        ledger._records.clear()
        ledger.record(task_id="T001", title="First", model="gpt-4", provider="openai", cost_usd=0.006)
        with pytest.raises(BudgetExceededError):
            ledger.record(task_id="T002", title="Second", model="gpt-4", provider="openai", cost_usd=0.006)


def test_record_within_budget_succeeds(tmp_path: Path):
    with patch("orchid.cost.ledger.get") as mock_get:
        mock_get.side_effect = lambda key, default=None: {
            "cost.budget_usd": 1.0, "cost.budget_warn_pct": 0.8,
        }.get(key, default)
        ledger = CostLedger(tmp_path / "within_budget_test")
        ledger._records.clear()
        rec = ledger.record(task_id="T001", title="Within budget", model="gpt-4", provider="openai", cost_usd=0.5)
        assert rec.cost_usd == 0.5


def test_record_no_budget_cap(tmp_path: Path):
    with patch("orchid.cost.ledger.get") as mock_get:
        mock_get.side_effect = lambda key, default=None: {
            "cost.budget_usd": None, "cost.budget_warn_pct": 0.8,
        }.get(key, default)
        ledger = CostLedger(tmp_path / "no_budget_test")
        ledger._records.clear()
        rec = ledger.record(task_id="T001", title="No cap", model="gpt-4", provider="openai", cost_usd=9999.0)
        assert rec.cost_usd == 9999.0


def test_get_totals_empty(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    totals = ledger.get_totals()
    assert totals["total_records"] == 0
    assert totals["total_input_tokens"] == 0
    assert totals["total_output_tokens"] == 0
    assert totals["total_tokens"] == 0
    assert totals["total_cost_usd"] == 0.0
    assert totals["per_model"] == {}
    assert totals["per_provider"] == {}


def test_get_totals_aggregates(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="Task 1", model="gpt-4", provider="openai",
                  input_tokens=1000, output_tokens=500, cost_usd=0.003)
    ledger.record(task_id="T002", title="Task 2", model="claude-sonnet-4-20250514", provider="anthropic",
                  input_tokens=2000, output_tokens=1000, cost_usd=0.006)
    totals = ledger.get_totals()
    assert totals["total_records"] == 2
    assert totals["total_input_tokens"] == 3000
    assert totals["total_output_tokens"] == 1500
    assert totals["total_tokens"] == 4500
    assert totals["total_cost_usd"] == 0.009


def test_get_totals_per_model(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="Task 1", model="gpt-4", provider="openai",
                  input_tokens=1000, output_tokens=500, cost_usd=0.003)
    ledger.record(task_id="T002", title="Task 2", model="gpt-4", provider="openai",
                  input_tokens=2000, output_tokens=1000, cost_usd=0.006)
    totals = ledger.get_totals()
    assert "gpt-4" in totals["per_model"]
    ms = totals["per_model"]["gpt-4"]
    assert ms["record_count"] == 2
    assert ms["total_tokens"] == 4500
    assert ms["total_cost_usd"] == 0.009


def test_get_totals_per_provider(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="Task 1", model="gpt-4", provider="openai",
                  input_tokens=1000, output_tokens=500, cost_usd=0.003)
    ledger.record(task_id="T002", title="Task 2", model="claude-sonnet-4-20250514", provider="anthropic",
                  input_tokens=2000, output_tokens=1000, cost_usd=0.006)
    totals = ledger.get_totals()
    assert "openai" in totals["per_provider"]
    assert "anthropic" in totals["per_provider"]
    assert totals["per_provider"]["openai"]["record_count"] == 1
    assert totals["per_provider"]["anthropic"]["record_count"] == 1


def test_get_totals_with_budget(tmp_path: Path):
    with patch("orchid.cost.ledger.get") as mock_get:
        mock_get.side_effect = lambda key, default=None: {
            "cost.budget_usd": 0.05, "cost.budget_warn_pct": 0.8,
        }.get(key, default)
        ledger = CostLedger(tmp_path / "budget_totals")
        ledger._records.clear()
        ledger.record(task_id="T001", title="Budget test", model="gpt-4", provider="openai", cost_usd=0.02)
        totals = ledger.get_totals()
        assert totals["budget_usd"] == 0.05
        assert totals["budget_remaining_usd"] == 0.03
        assert totals["budget_warn_pct"] is False


def test_get_totals_warn_pct_true(tmp_path: Path):
    with patch("orchid.cost.ledger.get") as mock_get:
        mock_get.side_effect = lambda key, default=None: {
            "cost.budget_usd": 0.01, "cost.budget_warn_pct": 0.8,
        }.get(key, default)
        ledger = CostLedger(tmp_path / "warn")
        ledger._records.clear()
        ledger.record(task_id="T001", title="Warn test", model="gpt-4", provider="openai", cost_usd=0.009)
        totals = ledger.get_totals()
        assert totals["budget_warn_pct"] is True


def test_get_records_for_task(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="T1", model="gpt-4", provider="openai")
    ledger.record(task_id="T002", title="T2", model="gpt-4", provider="openai")
    ledger.record(task_id="T001", title="T1 retry", model="gpt-4", provider="openai")
    recs = ledger.get_records_for_task("T001")
    assert len(recs) == 2
    assert all(r.task_id == "T001" for r in recs)
    recs_t2 = ledger.get_records_for_task("T002")
    assert len(recs_t2) == 1
    assert recs_t2[0].title == "T2"


def test_get_records_for_task_empty(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    assert ledger.get_records_for_task("T999") == []


def test_get_records_for_model(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="T1", model="gpt-4", provider="openai")
    ledger.record(task_id="T002", title="T2", model="claude-sonnet-4-20250514", provider="anthropic")
    ledger.record(task_id="T003", title="T3", model="gpt-4", provider="openai")
    recs = ledger.get_records_for_model("gpt-4")
    assert len(recs) == 2
    assert all(r.model == "gpt-4" for r in recs)


def test_get_records_for_model_empty(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    assert ledger.get_records_for_model("unknown-model") == []


def test_get_latest(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="First", model="gpt-4", provider="openai")
    ledger.record(task_id="T002", title="Second", model="gpt-4", provider="openai")
    ledger.record(task_id="T003", title="Third", model="gpt-4", provider="openai")
    latest = ledger.get_latest()
    assert latest.task_id == "T003"
    assert latest.title == "Third"


def test_get_latest_empty(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    assert ledger.get_latest() is None


def test_clear_removes_memory_records(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="T1", model="gpt-4", provider="openai")
    ledger.record(task_id="T002", title="T2", model="gpt-4", provider="openai")
    assert len(ledger._records) == 2
    ledger.clear()
    assert len(ledger._records) == 0
    assert ledger.get_latest() is None


def test_clear_does_not_delete_file(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="T1", model="gpt-4", provider="openai")
    fp = ledger._ledger_path
    assert fp.exists()
    ledger.clear()
    assert fp.exists()


def test_load_existing_records(tmp_path: Path):
    project_dir = tmp_path / "load_test"
    project_dir.mkdir(parents=True)
    ledger_path = project_dir / ".orchid" / "cost_ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"task_id": "T001", "title": "Loaded 1", "model": "gpt-4", "provider": "openai",
         "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
         "cost_usd": 0.001, "timestamp": "2025-01-01T00:00:00+00:00"},
        {"task_id": "T002", "title": "Loaded 2", "model": "claude-sonnet-4-20250514", "provider": "anthropic",
         "input_tokens": 200, "output_tokens": 100, "total_tokens": 300,
         "cost_usd": 0.002, "timestamp": "2025-01-01T01:00:00+00:00"},
    ]
    with ledger_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    ledger = CostLedger(project_dir)
    assert len(ledger._records) == 2
    assert ledger._records[0].task_id == "T001"
    assert ledger._records[1].task_id == "T002"


def test_load_skips_malformed_lines(tmp_path: Path):
    project_dir = tmp_path / "load_bad_test"
    project_dir.mkdir(parents=True)
    ledger_path = project_dir / ".orchid" / "cost_ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("w") as f:
        f.write(json.dumps({"task_id": "T001", "title": "Good", "model": "gpt-4", "provider": "openai",
                            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                            "cost_usd": 0.001, "timestamp": "2025-01-01T00:00:00+00:00"}) + "\n")
        f.write("this is not json\n")
        f.write(json.dumps({"task_id": "T002", "title": "Good2", "model": "gpt-4", "provider": "openai",
                            "input_tokens": 200, "output_tokens": 100, "total_tokens": 300,
                            "cost_usd": 0.002, "timestamp": "2025-01-01T01:00:00+00:00"}) + "\n")
    ledger = CostLedger(project_dir)
    assert len(ledger._records) == 2
    assert ledger._records[0].task_id == "T001"
    assert ledger._records[1].task_id == "T002"


def test_load_empty_file(tmp_path: Path):
    project_dir = tmp_path / "load_empty_test"
    project_dir.mkdir(parents=True)
    ledger_path = project_dir / ".orchid" / "cost_ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("")
    ledger = CostLedger(project_dir)
    assert len(ledger._records) == 0


def test_flush_handles_io_error(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    # First record to create the file
    ledger.record(task_id="T001", title="Pre", model="gpt-4", provider="openai")
    ledger._ledger_path.unlink()
    ledger._ledger_path.write_text("not a directory")
    ledger._flush({
        "task_id": "T001", "title": "Test", "model": "gpt-4", "provider": "openai",
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
        "timestamp": "2025-01-01T00:00:00+00:00",
    })


def test_concurrent_record_thread_safe(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    num_threads = 10
    results: list = []
    lock = threading.Lock()

    def worker(i: int):
        try:
            rec = ledger.record(task_id=f"T{i:03d}", title=f"Thread {i}", model="gpt-4",
                                provider="openai", input_tokens=100, output_tokens=50, cost_usd=0.001)
            with lock:
                results.append(rec.task_id)
        except Exception as e:
            with lock:
                results.append(f"ERROR:{e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == num_threads
    assert len(ledger._records) == num_threads
    lines = ledger._ledger_path.read_text().strip().split("\n")
    assert len(lines) == num_threads


def test_concurrent_get_totals_thread_safe(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    errors: list = []
    lock = threading.Lock()

    def recorder(i: int):
        try:
            ledger.record(task_id=f"T{i:03d}", title=f"Record {i}", model="gpt-4",
                          provider="openai", input_tokens=100, output_tokens=50, cost_usd=0.001)
        except Exception as e:
            with lock:
                errors.append(str(e))

    def total_reader():
        try:
            for _ in range(20):
                ledger.get_totals()
        except Exception as e:
            with lock:
                errors.append(str(e))

    threads = (
        [threading.Thread(target=recorder, args=(i,)) for i in range(5)] +
        [threading.Thread(target=total_reader) for _ in range(3)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(errors) == 0


def test_get_cost_ledger_requires_project_dir():
    reset_cost_ledger()
    with pytest.raises(CostLedgerError, match="requires a project_dir"):
        get_cost_ledger()


def test_get_cost_ledger_singleton(tmp_path: Path):
    reset_cost_ledger()
    project_dir = tmp_path / "singleton_test"
    project_dir.mkdir(parents=True)
    ledger1 = get_cost_ledger(project_dir)
    ledger2 = get_cost_ledger()
    assert ledger1 is ledger2
    reset_cost_ledger()


def test_configure_cost_ledger_replaces_singleton(tmp_path: Path):
    reset_cost_ledger()
    p1 = tmp_path / "config_test_1"
    p1.mkdir(parents=True)
    p2 = tmp_path / "config_test_2"
    p2.mkdir(parents=True)
    ledger1 = configure_cost_ledger(p1)
    ledger2 = configure_cost_ledger(p2)
    assert ledger1 is not ledger2
    assert ledger1._project_dir == p1
    assert ledger2._project_dir == p2
    reset_cost_ledger()


def test_reset_cost_ledger_clears_singleton(tmp_path: Path):
    reset_cost_ledger()
    pd = tmp_path / "reset_test"
    pd.mkdir(parents=True)
    ledger = get_cost_ledger(pd)
    assert ledger is not None
    reset_cost_ledger()
    with pytest.raises(CostLedgerError, match="requires a project_dir"):
        get_cost_ledger()


def test_reset_cost_ledger_is_safe_when_none():
    reset_cost_ledger()
    reset_cost_ledger()


def test_token_record_timestamp_is_fixed_at_creation():
    rec = TokenRecord(task_id="T001", title="Test", model="gpt-4", provider="openai")
    assert rec.timestamp is not None
    assert len(rec.timestamp) > 0


def test_budget_exceeded_error_is_subclass():
    assert issubclass(BudgetExceededError, CostLedgerError)


def test_budget_exceeded_error_message():
    err = BudgetExceededError("Task T001 cost $0.01 would exceed budget cap $0.005")
    assert "T001" in str(err)
    assert "budget" in str(err).lower()


def test_cost_ledger_record_token_fields(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    rec = ledger.record(task_id="T001", title="Spec test", model="claude-sonnet-4-20250514",
                        provider="anthropic", input_tokens=1000, output_tokens=500,
                        prompt_tokens=1000, cache_creation_input_tokens=200,
                        cache_read_input_tokens=300, completion_tokens=500, cost_usd=0.005)
    assert rec.input_tokens == 1000
    assert rec.output_tokens == 500
    assert rec.prompt_tokens == 1000
    assert rec.cache_creation_input_tokens == 200
    assert rec.cache_read_input_tokens == 300
    assert rec.completion_tokens == 500
    assert rec.cost_usd == 0.005


def test_cost_ledger_get_totals_returns_dict(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="Spec totals", model="gpt-4", provider="openai",
                  input_tokens=1000, output_tokens=500, cost_usd=0.003)
    totals = ledger.get_totals()
    assert isinstance(totals, dict)
    assert "total_records" in totals
    assert "total_input_tokens" in totals
    assert "total_output_tokens" in totals
    assert "total_tokens" in totals
    assert "total_cost_usd" in totals
    assert "per_model" in totals
    assert "per_provider" in totals


def test_cost_ledger_get_records_for_task_returns_list(tmp_path: Path):
    ledger = _make_ledger(tmp_path)
    ledger.record(task_id="T001", title="T1", model="gpt-4", provider="openai")
    recs = ledger.get_records_for_task("T001")
    assert isinstance(recs, list)
    assert all(isinstance(r, TokenRecord) for r in recs)


# ── Spec-required tests (T205) ────────────────────────────────────────────────

def test_record_creates_file(tmp_path):
    from orchid.cost.ledger import CostLedger
    ledger = CostLedger(tmp_path)
    ledger.record(task_id="T001", title="test", model="anthropic", provider="anthropic",
                  input_tokens=100, output_tokens=50, cost_usd=0.001)
    assert (tmp_path / ".orchid" / "cost_ledger.jsonl").exists()


def test_daily_spend_sums_today(tmp_path):
    import pytest

    from orchid.cost.ledger import CostLedger
    ledger = CostLedger(tmp_path)
    ledger.record(task_id="T001", title="t1", model="anthropic", provider="anthropic",
                  input_tokens=100, output_tokens=50, cost_usd=0.01)
    ledger.record(task_id="T002", title="t2", model="anthropic", provider="anthropic",
                  input_tokens=100, output_tokens=50, cost_usd=0.02)
    assert ledger.daily_spend("anthropic") == pytest.approx(0.03, abs=1e-6)


def test_daily_spend_ignores_other_providers(tmp_path):
    import pytest

    from orchid.cost.ledger import CostLedger
    ledger = CostLedger(tmp_path)
    ledger.record(task_id="T001", title="t1", model="anthropic", provider="anthropic",
                  input_tokens=100, output_tokens=50, cost_usd=0.05)
    ledger.record(task_id="T002", title="t2", model="local", provider="local",
                  input_tokens=100, output_tokens=50, cost_usd=0.01)
    assert ledger.daily_spend("anthropic") == pytest.approx(0.05)
    assert ledger.daily_spend("local") == pytest.approx(0.01)


def test_budget_remaining_returns_none_when_no_budget(tmp_path):
    from unittest.mock import patch

    from orchid.cost.ledger import CostLedger
    ledger = CostLedger(tmp_path)
    with patch("orchid.cost.ledger.get", return_value=None):
        result = ledger.budget_remaining("anthropic")
    assert result is None