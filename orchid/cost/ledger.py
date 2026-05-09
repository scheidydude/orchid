"""orchid/cost/ledger.py — Persistent token-cost ledger.

Responsibilities:
  - Record input/output/prompt/cache tokens per task execution
  - Persist ledger to <project>/.orchid/cost_ledger.jsonl (append-only)
  - Query totals by task, model, provider, date range
  - Track budget caps and alert when exceeded

Architecture:
  D0001: File-state — ledger lives in .orchid/cost_ledger.jsonl
  T200: Cost ledger — persistent per-project token accounting.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from orchid.config import get

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TokenRecord:
    """A single token-cost record for one task execution."""
    task_id: str
    title: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_tokens: int = 0       # alias for input_tokens (some providers use this name)
    cache_creation_input_tokens: int = 0  # cached prompt tokens (Anthropic)
    cache_read_input_tokens: int = 0      # cache read tokens (Anthropic)
    completion_tokens: int = 0   # alias for output_tokens
    total_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    user_id: str = ""

    @property
    def input_token_total(self) -> int:
        """Return the most specific input-token count available."""
        if self.cache_creation_input_tokens or self.cache_read_input_tokens:
            return self.cache_creation_input_tokens + self.cache_read_input_tokens
        return self.input_tokens or self.prompt_tokens

    @property
    def output_token_total(self) -> int:
        """Return the most specific output-token count available."""
        if self.completion_tokens:
            return self.completion_tokens
        return self.output_tokens

    @property
    def grand_total(self) -> int:
        """Total unique tokens across all categories.

        input_token_total already accounts for cache tokens, so we must NOT
        add cache_creation/cache_read again — that would double-count.
        """
        return self.input_token_total + self.output_token_total

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ── Exceptions ────────────────────────────────────────────────────────────────

class CostLedgerError(Exception):
    """Raised on ledger I/O or data corruption errors."""


class BudgetExceededError(CostLedgerError):
    """Raised when a task would exceed the configured budget cap."""


# ── Ledger ────────────────────────────────────────────────────────────────────

class CostLedger:
    """
    Append-only token-cost ledger for a single project.

    Ledger file: <project>/.orchid/cost_ledger.jsonl
    Each line is a JSON-serialised TokenRecord.

    Thread-safe via an internal RLock.
    """

    def __init__(self, project_dir: str | Path) -> None:
        self._project_dir = Path(project_dir)
        self._ledger_path = self._project_dir / ".orchid" / "cost_ledger.jsonl"
        self._lock = threading.RLock()
        self._records: list[TokenRecord] = []  # in-memory cache
        self._budget_usd: float | None = get("cost.budget_usd", None)
        self._budget_warn_pct: float = get("cost.budget_warn_pct", 0.8)
        self._load()  # read existing records on construction

    # ── Public API ──────────────────────────────────────────────────────────

    def record(
        self,
        task_id: str,
        title: str,
        model: str,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        prompt_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        user_id: str = "",
    ) -> TokenRecord:
        """
        Record a single token-cost entry.

        If a budget cap is configured, checks whether the cumulative spend
        would exceed it and raises BudgetExceededError if so.
        """
        record = TokenRecord(
            task_id=task_id,
            title=title,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_tokens=prompt_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            user_id=user_id,
        )

        with self._lock:
            # Budget check
            if self._budget_usd is not None:
                current_spend = sum(r.cost_usd for r in self._records)
                if current_spend + cost_usd > self._budget_usd:
                    logger.warning(
                        "BudgetExceeded: task %s cost $%.4f would push total to "
                        "$%.4f (cap $%.2f)",
                        task_id, cost_usd, current_spend + cost_usd, self._budget_usd,
                    )
                    raise BudgetExceededError(
                        f"Task {task_id} cost ${cost_usd:.4f} would exceed budget "
                        f"cap ${self._budget_usd:.2f}"
                    )

            self._records.append(record)
            self._flush(record.to_dict())

        logger.debug(
            "CostLedger recorded: %s model=%s in=%d out=%d cost=$%.4f",
            task_id, model, record.input_token_total, record.output_token_total, cost_usd,
        )
        return record

    def get_totals(self) -> dict[str, Any]:
        """Return aggregate totals across all recorded tasks."""
        with self._lock:
            totals = {
                "total_records": len(self._records),
                "total_input_tokens": sum(r.input_token_total for r in self._records),
                "total_output_tokens": sum(r.output_token_total for r in self._records),
                "total_cache_creation_tokens": sum(r.cache_creation_input_tokens for r in self._records),
                "total_cache_read_tokens": sum(r.cache_read_input_tokens for r in self._records),
                "total_tokens": sum(r.grand_total for r in self._records),
                "total_cost_usd": round(sum(r.cost_usd for r in self._records), 6),
            }
            # Per-model breakdown
            per_model: dict[str, dict[str, Any]] = {}
            for r in self._records:
                key = r.model
                if key not in per_model:
                    per_model[key] = {
                        "total_tokens": 0,
                        "total_cost_usd": 0.0,
                        "record_count": 0,
                    }
                per_model[key]["total_tokens"] += r.grand_total
                per_model[key]["total_cost_usd"] += r.cost_usd
                per_model[key]["record_count"] += 1
            # Round per-model costs
            for v in per_model.values():
                v["total_cost_usd"] = round(v["total_cost_usd"], 6)
            totals["per_model"] = per_model

            # Per-provider breakdown
            per_provider: dict[str, dict[str, Any]] = {}
            for r in self._records:
                key = r.provider
                if key not in per_provider:
                    per_provider[key] = {
                        "total_tokens": 0,
                        "total_cost_usd": 0.0,
                        "record_count": 0,
                    }
                per_provider[key]["total_tokens"] += r.grand_total
                per_provider[key]["total_cost_usd"] += r.cost_usd
                per_provider[key]["record_count"] += 1
            for v in per_provider.values():
                v["total_cost_usd"] = round(v["total_cost_usd"], 6)
            totals["per_provider"] = per_provider

            # Budget status
            if self._budget_usd is not None:
                spend_pct = totals["total_cost_usd"] / self._budget_usd
                totals["budget_usd"] = self._budget_usd
                totals["budget_remaining_usd"] = round(
                    max(0, self._budget_usd - totals["total_cost_usd"]), 6
                )
                totals["budget_warn_pct"] = spend_pct >= self._budget_warn_pct

            return totals

    def get_records_for_task(self, task_id: str) -> list[TokenRecord]:
        """Return all records for a specific task (a task may be retried)."""
        with self._lock:
            return [r for r in self._records if r.task_id == task_id]

    def get_records_for_model(self, model: str) -> list[TokenRecord]:
        """Return all records for a specific model."""
        with self._lock:
            return [r for r in self._records if r.model == model]

    def get_latest(self) -> TokenRecord | None:
        """Return the most recent record, or None if empty."""
        with self._lock:
            return self._records[-1] if self._records else None

    def clear(self) -> None:
        """Clear in-memory records. Does NOT delete the file on disk."""
        with self._lock:
            count = len(self._records)
            self._records.clear()
            logger.info("CostLedger cleared: %d records removed", count)

    # ── Spec-compatibility methods (T200 spec API) ─────────────────────────────

    def daily_spend(self, provider: str) -> float:
        """Return total cost_usd recorded today (UTC) for provider."""
        today = datetime.now(UTC).date().isoformat()
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if r.provider == provider and r.timestamp[:10] == today
            )

    def daily_spend_for_user(self, user_id: str) -> float:
        """Return total cost_usd recorded today (UTC) for a specific user."""
        today = datetime.now(UTC).date().isoformat()
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if r.user_id == user_id and r.timestamp[:10] == today
            )

    def daily_tokens(self, provider: str) -> int:
        """Return total input+output tokens recorded today (UTC) for provider."""
        today = datetime.now(UTC).date().isoformat()
        with self._lock:
            return sum(
                r.input_token_total + r.output_token_total
                for r in self._records
                if r.provider == provider and r.timestamp[:10] == today
            )

    def budget_remaining(self, provider: str) -> float | None:
        """Return remaining daily budget for provider, or None if no budget set."""
        budget = get(f"cost.daily_budget_usd.{provider}", None)
        if budget is None:
            return None
        return budget - self.daily_spend(provider)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Read existing ledger file into memory."""
        if not self._ledger_path.exists():
            return
        try:
            with self._ledger_path.open("r", encoding="utf-8") as fh:
                for line_num, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        self._records.append(TokenRecord.from_dict(data))
                    except json.JSONDecodeError:
                        logger.warning(
                            "CostLedger: skipping malformed line %d in %s",
                            line_num, self._ledger_path,
                        )
            logger.info(
                "CostLedger loaded %d record(s) from %s",
                len(self._records), self._ledger_path,
            )
        except Exception as exc:
            logger.warning("CostLedger: failed to load %s: %s", self._ledger_path, exc)

    def _flush(self, record_dict: dict[str, Any]) -> None:
        """Append a single record dict to the ledger file."""
        try:
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._ledger_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record_dict) + "\n")
        except Exception as exc:
            logger.warning("CostLedger: failed to flush record: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────────

_ledger_instance: CostLedger | None = None
_ledger_lock = threading.Lock()


def get_cost_ledger(project_dir: str | Path | None = None) -> CostLedger:
    """
    Return the global CostLedger singleton.

    If project_dir is provided, creates a new ledger for that project.
    Otherwise returns the existing global ledger (which must have been
    initialised with a project_dir).
    """
    global _ledger_instance
    if _ledger_instance is None:
        with _ledger_lock:
            if _ledger_instance is None:
                if project_dir is None:
                    raise CostLedgerError(
                        "CostLedger requires a project_dir — call with "
                        "get_cost_ledger(project_dir) or set one via "
                        "configure_cost_ledger(project_dir)"
                    )
                _ledger_instance = CostLedger(project_dir)
    return _ledger_instance


def configure_cost_ledger(project_dir: str | Path) -> CostLedger:
    """
    Create (or replace) the global CostLedger for a specific project.
    Call this at session start.
    """
    global _ledger_instance
    with _ledger_lock:
        if _ledger_instance is not None:
            _ledger_instance.clear()
        _ledger_instance = CostLedger(project_dir)
    return _ledger_instance


def reset_cost_ledger() -> None:
    """Reset the global ledger. Call when session ends."""
    global _ledger_instance
    with _ledger_lock:
        if _ledger_instance is not None:
            _ledger_instance.clear()
            _ledger_instance = None
        logger.info("Global CostLedger reset")