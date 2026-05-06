"""Circuit breaker for hook HTTP handlers.

Implements the circuit-breaker pattern to prevent cascading failures when
an external HTTP endpoint (e.g. Slack webhook, Telegram bot API) is
unreachable or returning persistent errors.

States:
    CLOSED   — normal operation, requests pass through.
    OPEN     — failures exceeded threshold; requests are rejected immediately.
    HALF_OPEN — after cooldown, one probe request is allowed through.
                Success → CLOSED. Failure → OPEN again.

Configuration (from .orchid.yaml):

    hooks:
      circuit_breaker:
        enabled: true
        failure_threshold: 5
        recovery_timeout: 60          # seconds before HALF_OPEN
        half_open_max_calls: 1        # probe calls allowed in HALF_OPEN
        success_threshold: 1          # consecutive successes to close
        monitored_events:
          - task_complete             # events whose HTTP hooks are monitored
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── State ────────────────────────────────────────────────────────────────────

class CircuitState(enum.Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# ── Data ─────────────────────────────────────────────────────────────────────

@dataclass
class CircuitBreakerConfig:
    """Configuration for a single circuit breaker instance."""
    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout: float = 60.0  # seconds
    half_open_max_calls: int = 1
    success_threshold: int = 1
    monitored_events: list[str] = field(default_factory=lambda: [
        "task_complete",
        "task_failed",
        "phase_transition",
    ])


@dataclass
class CircuitBreakerState:
    """Runtime state for one circuit breaker."""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)
    half_open_calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


# ── Singleton Registry ───────────────────────────────────────────────────────

class CircuitBreakerRegistry:
    """Registry of circuit breakers keyed by event type.

    Each event type that fires HTTP hooks gets its own breaker so that a
    flaky Slack webhook does not block, say, a Telegram notification.
    """

    _instance: CircuitBreakerRegistry | None = None

    def __new__(cls) -> CircuitBreakerRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._breakers: dict[str, CircuitBreakerState] = {}
        self._config: CircuitBreakerConfig = CircuitBreakerConfig()
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────────────

    def configure(self, config: CircuitBreakerConfig) -> None:
        """Replace the global configuration."""
        with self._lock:
            self._config = config

    @property
    def config(self) -> CircuitBreakerConfig:
        return self._config

    def get_breaker(self, event_type: str) -> CircuitBreakerState:
        """Return (or create) the breaker for *event_type*."""
        if not self._config.enabled:
            return CircuitBreakerState(state=CircuitState.CLOSED)

        with self._lock:
            if event_type not in self._breakers:
                self._breakers[event_type] = CircuitBreakerState()
            return self._breakers[event_type]

    def record_success(self, event_type: str) -> None:
        """Mark a successful call for the given event type."""
        if not self._config.enabled:
            return

        breaker = self.get_breaker(event_type)
        with breaker.lock:
            if breaker.state == CircuitState.HALF_OPEN:
                breaker.success_count += 1
                if breaker.success_count >= self._config.success_threshold:
                    self._transition(breaker, CircuitState.CLOSED)
            elif breaker.state == CircuitState.CLOSED:
                breaker.failure_count = 0  # reset on success

    def record_failure(self, event_type: str) -> None:
        """Mark a failed call for the given event type."""
        if not self._config.enabled:
            return

        breaker = self.get_breaker(event_type)
        with breaker.lock:
            if breaker.state == CircuitState.HALF_OPEN:
                # One failure in HALF_OPEN → back to OPEN
                self._transition(breaker, CircuitState.OPEN)
                return

            if breaker.state == CircuitState.OPEN:
                # Already open — just update timestamp
                breaker.last_failure_time = time.time()
                return

            # CLOSED
            breaker.failure_count += 1
            breaker.last_failure_time = time.time()
            if breaker.failure_count >= self._config.failure_threshold:
                self._transition(breaker, CircuitState.OPEN)

    def allow_request(self, event_type: str) -> bool:
        """Check whether a request should be allowed through.

        Returns True if the circuit is CLOSED or (in HALF_OPEN) the probe
        limit has not been reached.  Returns False if OPEN and cooldown
        has not elapsed.
        """
        if not self._config.enabled:
            return True

        breaker = self.get_breaker(event_type)
        with breaker.lock:
            if breaker.state == CircuitState.CLOSED:
                return True

            if breaker.state == CircuitState.OPEN:
                elapsed = time.time() - breaker.last_state_change
                if elapsed >= self._config.recovery_timeout:
                    self._transition(breaker, CircuitState.HALF_OPEN)
                    breaker.half_open_calls = 1
                    return True
                return False

            # HALF_OPEN
            if breaker.half_open_calls < self._config.half_open_max_calls:
                breaker.half_open_calls += 1
                return True
            return False

    def reset(self, event_type: str | None = None) -> None:
        """Reset one or all breakers to CLOSED."""
        with self._lock:
            if event_type is None:
                self._breakers.clear()
                logger.info("CircuitBreakerRegistry: reset all breakers")
            else:
                if event_type in self._breakers:
                    self._breakers[event_type] = CircuitBreakerState()
                    logger.info("CircuitBreakerRegistry: reset breaker for %s", event_type)

    def get_state(self, event_type: str) -> dict[str, Any]:
        """Return a serialisable snapshot of the breaker state."""
        breaker = self.get_breaker(event_type)
        with breaker.lock:
            return {
                "event_type": event_type,
                "state": breaker.state.value,
                "failure_count": breaker.failure_count,
                "success_count": breaker.success_count,
                "last_failure_time": breaker.last_failure_time,
                "last_state_change": breaker.last_state_change,
                "half_open_calls": breaker.half_open_calls,
            }

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        """Return snapshots for every registered breaker."""
        return {
            et: self.get_state(et) for et in self._breakers
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _transition(self, breaker: CircuitBreakerState, new_state: CircuitState) -> None:
        """Transition *breaker* to *new_state*."""
        old = breaker.state
        breaker.state = new_state
        breaker.last_state_change = time.time()
        if new_state == CircuitState.CLOSED:
            breaker.failure_count = 0
            breaker.success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            breaker.success_count = 0
            breaker.half_open_calls = 0
        logger.info(
            "CircuitBreaker %s: %s → %s",
            id(breaker), old.value, new_state.value,
        )


# ── Module-level convenience ─────────────────────────────────────────────────

_registry: CircuitBreakerRegistry | None = None


def _get_registry() -> CircuitBreakerRegistry:
    global _registry
    if _registry is None:
        _registry = CircuitBreakerRegistry()
    return _registry


# Public aliases (backwards-compatible with T152 wiring)
def configure_circuit_breaker(cfg: CircuitBreakerConfig) -> None:
    _get_registry().configure(cfg)


def allow_request(event_type: str) -> bool:
    return _get_registry().allow_request(event_type)


def record_success(event_type: str) -> None:
    _get_registry().record_success(event_type)


def record_failure(event_type: str) -> None:
    _get_registry().record_failure(event_type)


def reset_circuit_breaker(event_type: str | None = None) -> None:
    _get_registry().reset(event_type)


def get_circuit_state(event_type: str) -> dict[str, Any]:
    return _get_registry().get_state(event_type)


def get_all_circuit_states() -> dict[str, dict[str, Any]]:
    return _get_registry().get_all_states()


def get_circuit_breaker_config() -> CircuitBreakerConfig:
    return _get_registry().config