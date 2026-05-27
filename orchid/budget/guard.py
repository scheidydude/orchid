"""Budget enforcement + vault credential injection for task execution (Phase 5).

Design
------
- ``vault_env_context`` injects per-user LLM API keys from the credential vault
  into a **thread-local** override dict so concurrent cron jobs don't corrupt
  each other's environment.
- ``get_env`` replaces ``os.environ.get`` in execution paths that need per-user
  keys (e.g. Anthropic client init in agent_tool tasks).
- ``BudgetGuard`` checks and records USD spend against ``User.budget_used_usd``.
  ``budget_usd == 0`` means unlimited.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from typing import Generator

logger = logging.getLogger(__name__)

# ── Thread-local vault overrides ──────────────────────────────────────────────
# Cron jobs run on the APScheduler thread pool; using os.environ directly would
# allow Job A's API key to bleed into Job B running on a different thread.
# Instead we store per-thread overrides here and read them via get_env().

_task_local = threading.local()


def get_env(key: str, default: str | None = None) -> str | None:
    """Return env var, preferring per-thread vault overrides over os.environ."""
    overrides: dict[str, str] = getattr(_task_local, "env_overrides", {})
    if key in overrides:
        return overrides[key]
    return os.environ.get(key, default)


# ── Env-var names that may hold provider API keys ─────────────────────────────

_PROVIDER_ENV_VARS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "COHERE_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "REPLICATE_API_KEY",
        "HUGGINGFACE_API_KEY",
    }
)


@contextlib.contextmanager
def vault_env_context(
    owner_id: str,
    vault_store=None,
) -> Generator[dict[str, str], None, None]:
    """Temporarily set thread-local env overrides from the user's credential vault.

    Matches vault keys against ``_PROVIDER_ENV_VARS``; injects matching keys.
    Yields the injected ``{key: value}`` dict (empty if nothing matched or vault
    unavailable).  Never raises — vault errors are logged at DEBUG and swallowed.
    """
    injected: dict[str, str] = {}

    if vault_store is None:
        try:
            from orchid.vault.store import get_vault

            vault_store = get_vault()
        except Exception as exc:  # noqa: BLE001
            logger.debug("vault_env_context: vault module unavailable: %s", exc)
            yield injected
            return

    try:
        for key in vault_store.list_keys(owner_id):
            if key in _PROVIDER_ENV_VARS:
                val = vault_store.get(owner_id, key)
                if val:
                    injected[key] = val
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "vault_env_context: failed to load vault for %s: %s", owner_id, exc
        )
        yield {}
        return

    if not injected:
        yield injected
        return

    prev: dict[str, str] = getattr(_task_local, "env_overrides", {})
    _task_local.env_overrides = {**prev, **injected}
    try:
        yield injected
    finally:
        _task_local.env_overrides = prev


# ── Anthropic token pricing ───────────────────────────────────────────────────
# USD per 1 M tokens.  Keys are model name prefixes; first match wins.
# Values from Anthropic pricing page as of 2026-05.

_ANTHROPIC_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4":     (15.00, 75.00),
    "claude-sonnet-4":   (3.00,  15.00),
    "claude-haiku-4":    (0.80,   4.00),
    "claude-3-5-sonnet": (3.00,  15.00),
    "claude-3-5-haiku":  (0.80,   4.00),
    "claude-3-opus":     (15.00, 75.00),
    "claude-3-sonnet":   (3.00,  15.00),
    "claude-3-haiku":    (0.25,   1.25),
}


def _compute_anthropic_cost(
    model: str, input_tokens: int, output_tokens: int
) -> float:
    """Return estimated USD cost for one Anthropic API call."""
    input_price = output_price = 0.0
    for prefix, (ip, op) in _ANTHROPIC_PRICING.items():
        if model.startswith(prefix):
            input_price, output_price = ip, op
            break
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000.0


# ── Budget guard ──────────────────────────────────────────────────────────────


class BudgetExceededError(Exception):
    """Raised when a user has exhausted their LLM budget."""

    def __init__(self, limit: float, used: float) -> None:
        self.limit = limit
        self.used = used
        super().__init__(
            f"LLM budget exceeded: used ${used:.4f} of ${limit:.4f} limit"
        )


class BudgetGuard:
    """Check and record LLM spend for a single user.

    Args:
        owner_id: Orchid user_id whose budget to track.
        store:    ``BaseUserStore`` instance.  Defaults to the process singleton.
    """

    def __init__(self, owner_id: str, store=None) -> None:
        self._owner_id = owner_id
        self._store = store

    def _get_store(self):
        if self._store is not None:
            return self._store
        from orchid.auth.store import get_store

        return get_store()

    def check(self) -> None:
        """Raise ``BudgetExceededError`` if the user is over their budget.

        ``budget_usd <= 0`` means unlimited and will never raise.
        An unknown ``owner_id`` is treated as unlimited.
        """
        store = self._get_store()
        user = store.get_user(self._owner_id)
        if user is None or user.budget_usd <= 0:
            return  # unlimited
        if user.budget_used_usd >= user.budget_usd:
            raise BudgetExceededError(
                limit=user.budget_usd, used=user.budget_used_usd
            )

    def record(self, cost_usd: float) -> None:
        """Add *cost_usd* to the user's accumulated spend.

        Safe to call from any thread; stores are internally lock-protected.
        A zero or negative cost is silently ignored.
        """
        if cost_usd <= 0:
            return
        store = self._get_store()
        user = store.get_user(self._owner_id)
        if user is None:
            return
        user.budget_used_usd = round(user.budget_used_usd + cost_usd, 8)
        store.update_user(user)
        logger.debug(
            "BudgetGuard.record: %s += $%.6f (total $%.6f)",
            self._owner_id,
            cost_usd,
            user.budget_used_usd,
        )

    def remaining(self) -> float | None:
        """Return remaining budget in USD, or ``None`` if unlimited."""
        store = self._get_store()
        user = store.get_user(self._owner_id)
        if user is None or user.budget_usd <= 0:
            return None
        return max(0.0, user.budget_usd - user.budget_used_usd)

    # ── CPU budget ────────────────────────────────────────────────────────────

    @staticmethod
    def _today() -> str:
        """Return today's date as 'YYYY-MM-DD' (UTC)."""
        from datetime import UTC, datetime
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _reset_cpu_if_new_day(self, user) -> bool:
        """Reset cpu_used_seconds if the stored date != today. Returns True if reset."""
        today = self._today()
        if user.cpu_last_reset_date != today:
            user.cpu_used_seconds = 0.0
            user.cpu_last_reset_date = today
            return True
        return False

    def check_cpu(self) -> None:
        """Raise ``BudgetExceededError`` if the user is over their daily CPU cap.

        ``cpu_budget_seconds <= 0`` means unlimited.
        Auto-resets counter at UTC midnight.
        """
        store = self._get_store()
        user = store.get_user(self._owner_id)
        if user is None or user.cpu_budget_seconds <= 0:
            return  # unlimited
        reset = self._reset_cpu_if_new_day(user)
        if reset:
            store.update_user(user)
            return  # just reset — clearly under budget
        if user.cpu_used_seconds >= user.cpu_budget_seconds:
            raise BudgetExceededError(
                limit=user.cpu_budget_seconds, used=user.cpu_used_seconds
            )

    def record_cpu(self, seconds: float) -> None:
        """Add *seconds* of wall-clock time to the user's daily CPU usage.

        Auto-resets the counter if it's a new UTC day.
        """
        if seconds <= 0:
            return
        store = self._get_store()
        user = store.get_user(self._owner_id)
        if user is None:
            return
        self._reset_cpu_if_new_day(user)
        user.cpu_used_seconds = round(user.cpu_used_seconds + seconds, 4)
        store.update_user(user)

    def remaining_cpu(self) -> float | None:
        """Return remaining CPU seconds today, or ``None`` if unlimited."""
        store = self._get_store()
        user = store.get_user(self._owner_id)
        if user is None or user.cpu_budget_seconds <= 0:
            return None
        self._reset_cpu_if_new_day(user)
        return max(0.0, user.cpu_budget_seconds - user.cpu_used_seconds)
