"""orchid/cost/scheduler.py — Cost-aware task scheduling.

Responsibilities:
  - Track cumulative token cost per model/provider across a session
  - Enforce budget caps (hard stop) and soft warnings
  - Prefer cheaper providers when budget is constrained
  - Detect 429 rate-limit responses and auto-throttle scheduling
  - Provide per-model/provider spend summaries to the orchestrator

Architecture:
  D0001: File-state — ledger lives in .orchid/cost_ledger.jsonl
  T201: Cost scheduler — cost-aware scheduling with budget enforcement.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from orchid import config as cfg

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SpendSnapshot:
    """Immutable snapshot of cumulative spend across all models/providers."""
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    per_model: dict[str, float] = field(default_factory=dict)  # model -> cost_usd
    per_provider: dict[str, float] = field(default_factory=dict)  # provider -> cost_usd
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def budget_remaining_usd(self) -> float:
        cap = cfg.get("cost.budget_usd", None)
        if cap is None:
            return float("inf")
        return max(0.0, cap - self.total_cost_usd)

    @property
    def budget_warn_pct(self) -> float:
        cap = cfg.get("cost.budget_usd", None)
        if cap is None:
            return 0.0
        return self.total_cost_usd / cap if cap > 0 else 0.0

    @property
    def budget_warn_triggered(self) -> bool:
        warn_pct = cfg.get("cost.budget_warn_pct", 0.8)
        return self.budget_warn_pct >= warn_pct

    @property
    def budget_exceeded(self) -> bool:
        cap = cfg.get("cost.budget_usd", None)
        if cap is None:
            return False
        return self.total_cost_usd > cap


@dataclass
class RateLimitState:
    """Tracks 429 rate-limit occurrences and back-off state."""
    consecutive_429: int = 0
    last_429_at: float = 0.0
    backoff_until: float = 0.0
    throttle_active: bool = False

    @property
    def is_throttled(self) -> bool:
        return self.throttle_active and time.monotonic() < self.backoff_until

    @property
    def backoff_remaining(self) -> float:
        if not self.is_throttled:
            return 0.0
        return max(0.0, self.backoff_until - time.monotonic())


# ── Exceptions ────────────────────────────────────────────────────────────────

class BudgetBlockedError(Exception):
    """Raised when a task cannot proceed because the budget cap is exceeded."""


class ThrottleBlockedError(Exception):
    """Raised when a task is blocked due to rate-limit back-off."""


# ── CostScheduler ─────────────────────────────────────────────────────────────

class CostScheduler:
    """
    Cost-aware scheduler that sits between the orchestrator and task dispatch.

    On every task execution it:
      1. Checks if the budget cap is exceeded -> raises BudgetBlockedError
      2. Checks if rate-limit back-off is active -> raises ThrottleBlockedError
      3. Selects the cheapest available provider when budget is tight
      4. Records token/cost data after the task completes

    Thread-safe via an internal RLock.
    """

    def __init__(self, project_dir: str | None = None) -> None:
        self._lock = threading.RLock()
        self._project_dir = project_dir
        self._snapshot = SpendSnapshot()
        self._rate_limit = RateLimitState()
        self._warned_budget: bool = False
        self._budget_stop: bool = cfg.get("cost.budget_stop", True)

        # Load existing ledger data if project_dir is provided
        if project_dir is not None:
            from orchid.cost.ledger import get_cost_ledger
            try:
                ledger = get_cost_ledger(project_dir)
                totals = ledger.get_totals()
                # Extract per-model/per-provider cost from nested dicts
                per_model: dict[str, float] = {}
                for m, data in totals.get("per_model", {}).items():
                    per_model[m] = data.get("total_cost_usd", 0.0)
                per_provider: dict[str, float] = {}
                for p, data in totals.get("per_provider", {}).items():
                    per_provider[p] = data.get("total_cost_usd", 0.0)
                self._snapshot = SpendSnapshot(
                    total_cost_usd=totals.get("total_cost_usd", 0.0),
                    total_input_tokens=totals.get("total_input_tokens", 0),
                    total_output_tokens=totals.get("total_output_tokens", 0),
                    total_tokens=totals.get("total_tokens", 0),
                    per_model=per_model,
                    per_provider=per_provider,
                )
                logger.info(
                    "CostScheduler loaded spend: $%.4f (%d tokens) from ledger",
                    self._snapshot.total_cost_usd, self._snapshot.total_tokens,
                )
            except Exception as exc:
                logger.warning("CostScheduler: failed to load ledger: %s", exc)

    # ── Pre-execution checks ────────────────────────────────────────────────

    def check_budget(self) -> None:
        """
        Raise BudgetBlockedError if the budget cap is exceeded.

        Only enforced when cost.budget_usd is set and cost.budget_stop is True.
        """
        with self._lock:
            if not self._budget_stop:
                return
            if self._snapshot.budget_exceeded:
                logger.warning(
                    "BudgetBlocked: total spend $%.4f exceeds cap $%.2f — "
                    "no further tasks will be dispatched.",
                    self._snapshot.total_cost_usd,
                    cfg.get("cost.budget_usd", 0.0),
                )
                raise BudgetBlockedError(
                    f"Budget cap ${cfg.get('cost.budget_usd', 0.0):.2f} exceeded "
                    f"(spend: ${self._snapshot.total_cost_usd:.4f}). "
                    "Set cost.budget_stop=false to disable."
                )

    def check_rate_limit(self) -> None:
        """
        Raise ThrottleBlockedError if rate-limit back-off is active.

        Back-off duration increases with consecutive 429s:
          1st 429:  5s
          2nd 429:  10s
          3rd 429:  20s
          4th+ 429: 60s (capped)
        """
        with self._lock:
            if self._rate_limit.is_throttled:
                remaining = self._rate_limit.backoff_remaining
                logger.warning(
                    "RateLimitThrottle: %d consecutive 429s — back-off %.1fs remaining",
                    self._rate_limit.consecutive_429, remaining,
                )
                raise ThrottleBlockedError(
                    f"Rate-limit throttle active — {remaining:.1f}s back-off remaining "
                    f"({self._rate_limit.consecutive_429} consecutive 429s)"
                )

    def record_429(self) -> None:
        """
        Record a 429 rate-limit response and compute back-off.

        Resets consecutive count on any non-429 call to reset_429().
        """
        with self._lock:
            self._rate_limit.consecutive_429 += 1
            self._rate_limit.last_429_at = time.monotonic()

            # Exponential back-off: 5, 10, 20, 60, 60, ...
            base = min(5 * (2 ** (self._rate_limit.consecutive_429 - 1)), 60)
            jitter = random.uniform(0, 2)
            self._rate_limit.backoff_until = time.monotonic() + base + jitter
            self._rate_limit.throttle_active = True

            logger.warning(
                "RateLimit: 429 detected (consecutive=%d, back-off=%.1fs)",
                self._rate_limit.consecutive_429, base + jitter,
            )

    def reset_429(self) -> None:
        """Reset the rate-limit counter after a successful request."""
        with self._lock:
            if self._rate_limit.consecutive_429 > 0:
                logger.debug(
                    "RateLimit: reset after %d consecutive 429s",
                    self._rate_limit.consecutive_429,
                )
            self._rate_limit.consecutive_429 = 0
            self._rate_limit.throttle_active = False
            self._rate_limit.backoff_until = 0.0

    # ── Provider selection ──────────────────────────────────────────────────

    def select_cheapest_provider(
        self,
        available_providers: list[str],
        task_type: str,
    ) -> str | None:
        """
        Return the cheapest available provider name from the list.

        If only one provider is available, returns it directly.
        If budget is tight (warn_pct >= 0.5), prefers the cheapest option.
        Otherwise falls back to the first available provider.

        Provider cost ranking is based on historical spend per model:
          - Lower total spend per model -> cheaper (proxy for cost efficiency)
          - "local" is always preferred when available and budget is tight
        """
        with self._lock:
            if len(available_providers) <= 1:
                return available_providers[0] if available_providers else None

            # If budget is not tight, just return the first available
            if self._snapshot.budget_warn_pct < 0.5:
                return available_providers[0]

            # Budget is tight -> prefer "local" if available
            if "local" in available_providers:
                return "local"

            # Fall back to cheapest by historical spend
            cheapest = min(
                available_providers,
                key=lambda p: self._snapshot.per_model.get(p, 0.0),
            )
            return cheapest

    # ── Post-execution recording ────────────────────────────────────────────

    def record_cost(
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
    ) -> SpendSnapshot:
        """
        Record cost data and return the updated snapshot.

        If a CostLedger is available, delegates recording to it.
        Also updates the in-memory snapshot for immediate budget checks.
        """
        with self._lock:
            self._snapshot.total_cost_usd += cost_usd
            self._snapshot.total_input_tokens += input_tokens
            self._snapshot.total_output_tokens += output_tokens
            self._snapshot.total_tokens += input_tokens + output_tokens
            self._snapshot.per_model[model] = (
                self._snapshot.per_model.get(model, 0.0) + cost_usd
            )
            self._snapshot.per_provider[provider] = (
                self._snapshot.per_provider.get(provider, 0.0) + cost_usd
            )

            # Delegate to ledger if available
            if self._project_dir is not None:
                from orchid.cost.ledger import get_cost_ledger
                try:
                    ledger = get_cost_ledger(self._project_dir)
                    ledger.record(
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
                    )
                except Exception as exc:
                    logger.warning("CostScheduler: ledger record failed: %s", exc)

            # Budget warning (once per session)
            if self._snapshot.budget_warn_triggered and not self._warned_budget:
                self._warned_budget = True
                logger.warning(
                    "BudgetWarning: spend has reached %.0f%% of cap $%.2f "
                    "(current: $%.4f)",
                    self._snapshot.budget_warn_pct * 100,
                    cfg.get("cost.budget_usd", 0.0),
                    self._snapshot.total_cost_usd,
                )

            logger.debug(
                "CostScheduler recorded: %s model=%s cost=$%.4f "
                "(cumulative: $%.4f)",
                task_id, model, cost_usd, self._snapshot.total_cost_usd,
            )
            return SpendSnapshot(
                total_cost_usd=self._snapshot.total_cost_usd,
                total_input_tokens=self._snapshot.total_input_tokens,
                total_output_tokens=self._snapshot.total_output_tokens,
                total_tokens=self._snapshot.total_tokens,
                per_model=dict(self._snapshot.per_model),
                per_provider=dict(self._snapshot.per_provider),
            )

    # ── Status / introspection ──────────────────────────────────────────────

    def get_snapshot(self) -> SpendSnapshot:
        """Return a copy of the current spend snapshot."""
        with self._lock:
            return SpendSnapshot(
                total_cost_usd=self._snapshot.total_cost_usd,
                total_input_tokens=self._snapshot.total_input_tokens,
                total_output_tokens=self._snapshot.total_output_tokens,
                total_tokens=self._snapshot.total_tokens,
                per_model=dict(self._snapshot.per_model),
                per_provider=dict(self._snapshot.per_provider),
            )

    def get_rate_limit_state(self) -> RateLimitState:
        """Return a copy of the current rate-limit state."""
        with self._lock:
            return RateLimitState(
                consecutive_429=self._rate_limit.consecutive_429,
                last_429_at=self._rate_limit.last_429_at,
                backoff_until=self._rate_limit.backoff_until,
                throttle_active=self._rate_limit.throttle_active,
            )

    def summary(self) -> dict[str, Any]:
        """Return a human-readable summary dict for logging / web UI."""
        with self._lock:
            cap = cfg.get("cost.budget_usd", None)
            return {
                "total_cost_usd": round(self._snapshot.total_cost_usd, 6),
                "total_input_tokens": self._snapshot.total_input_tokens,
                "total_output_tokens": self._snapshot.total_output_tokens,
                "total_tokens": self._snapshot.total_tokens,
                "budget_usd": cap,
                "budget_remaining_usd": round(self._snapshot.budget_remaining_usd, 6) if cap else None,
                "budget_warn_pct": round(self._snapshot.budget_warn_pct, 4),
                "budget_warn_triggered": self._snapshot.budget_warn_triggered,
                "budget_exceeded": self._snapshot.budget_exceeded,
                "per_model": {k: round(v, 6) for k, v in self._snapshot.per_model.items()},
                "per_provider": {k: round(v, 6) for k, v in self._snapshot.per_provider.items()},
                "rate_limit": {
                    "consecutive_429": self._rate_limit.consecutive_429,
                    "throttle_active": self._rate_limit.throttle_active,
                    "backoff_remaining_s": round(self._rate_limit.backoff_remaining, 1),
                },
                "timestamp": self._snapshot.timestamp,
            }

    def reset(self) -> None:
        """Reset all state. Call at session start."""
        with self._lock:
            self._snapshot = SpendSnapshot()
            self._rate_limit = RateLimitState()
            self._warned_budget = False
            logger.info("CostScheduler reset")


# ── Module-level singleton ────────────────────────────────────────────────────

_scheduler_instance: CostScheduler | None = None
_scheduler_lock = threading.Lock()


def get_cost_scheduler(project_dir: str | None = None) -> CostScheduler:
    """
    Return the global CostScheduler singleton.

    If project_dir is provided and no singleton exists, creates one.
    """
    global _scheduler_instance  # noqa: PLW0603
    if _scheduler_instance is None:
        with _scheduler_lock:
            if _scheduler_instance is None:
                _scheduler_instance = CostScheduler(project_dir)
    return _scheduler_instance


def configure_cost_scheduler(project_dir: str | None = None) -> CostScheduler:
    """
    Create (or replace) the global CostScheduler.
    Call this at session start.
    """
    global _scheduler_instance  # noqa: PLW0603
    with _scheduler_lock:
        _scheduler_instance = CostScheduler(project_dir)
    return _scheduler_instance


def reset_cost_scheduler() -> None:
    """Reset and clear the global scheduler singleton."""
    global _scheduler_instance  # noqa: PLW0603
    with _scheduler_lock:
        if _scheduler_instance is not None:
            _scheduler_instance.reset()
            _scheduler_instance = None


# ── Spec-compatibility shims (T201 spec API) ──────────────────────────────────

# Thread-local rate-pressure flags for spec-required API.
_rate_flags: dict[str, bool] = {}
_rate_flags_lock = threading.Lock()


def set_rate_pressure(provider: str, limited: bool) -> None:
    """Set rate-limit pressure flag for a provider (spec-required module fn).

    Only updates the module-level _rate_flags dict. Does not touch the
    CostScheduler singleton — that has its own 429 state tracked independently.
    """
    with _rate_flags_lock:
        _rate_flags[provider] = limited


# CostAwareScheduler: spec-required alias for CostScheduler with added methods.
class CostAwareScheduler(CostScheduler):
    """Spec-required class name; extends CostScheduler with select_provider/record_usage."""

    def select_provider(
        self,
        candidates: list[str],
        task_type: str = "",
        task_priority: int = 2,
    ) -> str:
        """Return best provider from candidates list; skip over-budget or rate-limited."""
        from pathlib import Path
        for provider in candidates:
            # Budget check
            snap = self.get_snapshot()
            budget = cfg.get(f"cost.daily_budget_usd.{provider}", None)
            if budget is not None:
                spent = snap.total_cost_usd
                if spent >= budget:
                    continue
                # Local-pressure check
                if (
                    cfg.get("cost.prefer_local_under_pressure", False)
                    and provider != "local"
                    and (budget - spent) < cfg.get("cost.local_fallback_threshold_usd", 1.0)
                ):
                    continue
            # Rate pressure check
            with _rate_flags_lock:
                if _rate_flags.get(provider, False):
                    continue
            return provider
        # Fallback: return last candidate
        return candidates[-1]

    def record_usage(self, provider: str, input_tokens: int, output_tokens: int) -> None:
        """Record token usage (spec-required method); estimates cost from config."""
        input_price = cfg.get(f"cost.price_per_1k_tokens.{provider}.input", 0.0)
        output_price = cfg.get(f"cost.price_per_1k_tokens.{provider}.output", 0.0)
        cost_usd = (input_tokens / 1000) * input_price + (output_tokens / 1000) * output_price
        self.record_cost(
            model=provider,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )