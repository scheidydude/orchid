"""Unit tests for the Orchid V2 circuit breaker pattern.

Tests the full state machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED,
per-event-type isolation, singleton registry, module-level convenience
functions, and configuration toggling.
"""

import json
import threading
import time as _time
from unittest.mock import patch

import pytest

from orchid.hooks.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitBreakerState,
    CircuitState,
    _get_registry,
    allow_request,
    # module-level convenience functions
    configure_circuit_breaker,
    get_all_circuit_states,
    get_circuit_breaker_config,
    get_circuit_state,
    record_failure,
    record_success,
    reset_circuit_breaker,
)

# -- Helpers --

def _force_reset_module_state():
    """Reset the module-level singleton so tests are isolated."""
    import orchid.hooks.circuit_breaker as cb
    cb._registry = None
    # Also reset the class-level singleton instance
    CircuitBreakerRegistry._instance = None


@pytest.fixture(autouse=True)
def _reset_each_test():
    """Ensure each test starts with a clean circuit breaker registry."""
    _force_reset_module_state()
    yield
    _force_reset_module_state()


# -- CircuitState enum --

class TestCircuitState:
    """Tests for the CircuitState enum."""

    def test_enum_values(self):
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"

    def test_enum_membership(self):
        assert CircuitState.CLOSED in CircuitState
        assert CircuitState.OPEN in CircuitState
        assert CircuitState.HALF_OPEN in CircuitState

    def test_state_from_string(self):
        assert CircuitState("closed") == CircuitState.CLOSED
        assert CircuitState("open") == CircuitState.OPEN
        assert CircuitState("half_open") == CircuitState.HALF_OPEN


# -- CircuitBreakerConfig dataclass --

class TestCircuitBreakerConfig:
    """Tests for CircuitBreakerConfig defaults and overrides."""

    def test_default_config(self):
        cfg = CircuitBreakerConfig()
        assert cfg.enabled is True
        assert cfg.failure_threshold == 5
        assert cfg.recovery_timeout == 60.0
        assert cfg.half_open_max_calls == 1
        assert cfg.success_threshold == 1
        assert cfg.monitored_events == [
            "task_complete",
            "task_failed",
            "phase_transition",
        ]

    def test_custom_config(self):
        cfg = CircuitBreakerConfig(
            enabled=False,
            failure_threshold=3,
            recovery_timeout=30.0,
            half_open_max_calls=2,
            success_threshold=2,
            monitored_events=["custom_event"],
        )
        assert cfg.enabled is False
        assert cfg.failure_threshold == 3
        assert cfg.recovery_timeout == 30.0
        assert cfg.half_open_max_calls == 2
        assert cfg.success_threshold == 2
        assert cfg.monitored_events == ["custom_event"]


# -- CircuitBreakerState dataclass --

class TestCircuitBreakerState:
    """Tests for CircuitBreakerState runtime state."""

    def test_default_state(self):
        state = CircuitBreakerState()
        assert state.state == CircuitState.CLOSED
        assert state.failure_count == 0
        assert state.success_count == 0
        assert state.last_failure_time == 0.0
        assert state.half_open_calls == 0
        assert state.lock is not None

    def test_custom_initial_state(self):
        state = CircuitBreakerState(
            state=CircuitState.OPEN,
            failure_count=5,
            last_failure_time=1000.0,
        )
        assert state.state == CircuitState.OPEN
        assert state.failure_count == 5
        assert state.last_failure_time == 1000.0


# -- CircuitBreakerRegistry singleton --

class TestCircuitBreakerRegistry:
    """Tests for the CircuitBreakerRegistry singleton pattern."""

    def test_singleton(self):
        r1 = CircuitBreakerRegistry()
        r2 = CircuitBreakerRegistry()
        assert r1 is r2

    def test_double_init_is_noop(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=99))
        r2 = CircuitBreakerRegistry()
        assert r2.config.failure_threshold == 99

    def test_get_breaker_creates_on_demand(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        state = r.get_breaker("task_complete")
        assert state.state == CircuitState.CLOSED
        assert state.failure_count == 0

    def test_multiple_event_types_have_separate_breakers(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        s1 = r.get_breaker("event_a")
        s2 = r.get_breaker("event_b")
        assert s1 is not s2
        with s1.lock:
            s1.failure_count = 10
        with s2.lock:
            s2.failure_count = 0
        assert s1.failure_count == 10
        assert s2.failure_count == 0

    def test_disabled_registry_returns_transient_state(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(enabled=False))
        state = r.get_breaker("anything")
        assert state.state == CircuitState.CLOSED
        with state.lock:
            state.failure_count = 999
        state2 = r.get_breaker("anything")
        assert state2.failure_count == 0

    def test_record_success_disabled_is_noop(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(enabled=False))
        r.record_success("event_a")
        r.record_failure("event_a")
        assert len(r._breakers) == 0

    def test_allow_request_disabled_returns_true(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(enabled=False))
        assert r.allow_request("any_event") is True


# -- CLOSED -> OPEN transition --

class TestClosedToOpen:
    """Tests the CLOSED -> OPEN transition when failures exceed threshold."""

    def test_no_transition_under_threshold(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=5))
        for _ in range(4):
            r.record_failure("event_a")
        assert r.get_breaker("event_a").state == CircuitState.CLOSED

    def test_transition_to_open_at_threshold(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=5))
        for _ in range(5):
            r.record_failure("event_a")
        state = r.get_breaker("event_a")
        assert state.state == CircuitState.OPEN
        assert state.failure_count == 5

    def test_failure_count_reset_on_success(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=5))
        r.record_failure("event_a")
        r.record_failure("event_a")
        r.record_success("event_a")
        state = r.get_breaker("event_a")
        assert state.state == CircuitState.CLOSED
        assert state.failure_count == 0

    def test_failure_in_open_only_updates_timestamp(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=2, recovery_timeout=300))
        r.record_failure("event_a")
        r.record_failure("event_a")
        assert r.get_breaker("event_a").state == CircuitState.OPEN
        r.record_failure("event_a")
        r.record_failure("event_a")
        state = r.get_breaker("event_a")
        assert state.state == CircuitState.OPEN
        assert state.failure_count == 2


# -- OPEN -> HALF_OPEN transition --

class TestOpenToHalfOpen:
    """Tests the OPEN -> HALF_OPEN transition after recovery timeout."""

    def test_open_rejects_requests_before_timeout(self):
        with patch("orchid.hooks.circuit_breaker.time") as time_mock:
            time_mock.time.return_value = 1000.0
            r = CircuitBreakerRegistry()
            r.configure(CircuitBreakerConfig(failure_threshold=2, recovery_timeout=60))
            r.record_failure("event_a")
            r.record_failure("event_a")
            assert r.get_breaker("event_a").state == CircuitState.OPEN
            assert r.allow_request("event_a") is False

    def test_open_transitions_to_half_open_after_timeout(self):
        with patch("orchid.hooks.circuit_breaker.time") as time_mock:
            time_mock.time.return_value = 1000.0
            r = CircuitBreakerRegistry()
            r.configure(CircuitBreakerConfig(failure_threshold=2, recovery_timeout=5))
            r.record_failure("event_a")
            r.record_failure("event_a")
            state = r.get_breaker("event_a")
            with state.lock:
                state.last_state_change = 1000.0
            time_mock.time.return_value = 1006.0
            assert r.allow_request("event_a") is True
            state = r.get_breaker("event_a")
            assert state.state == CircuitState.HALF_OPEN

    def test_open_rejects_requests_after_timeout_if_probe_used(self):
        with patch("orchid.hooks.circuit_breaker.time") as time_mock:
            time_mock.time.return_value = 1000.0
            r = CircuitBreakerRegistry()
            r.configure(CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout=5,
                half_open_max_calls=1,
            ))
            r.record_failure("event_a")
            r.record_failure("event_a")
            state = r.get_breaker("event_a")
            with state.lock:
                state.last_state_change = 1000.0
            time_mock.time.return_value = 1006.0
            assert r.allow_request("event_a") is True
            state = r.get_breaker("event_a")
            with state.lock:
                assert state.half_open_calls == 1
            assert r.allow_request("event_a") is False


# -- HALF_OPEN -> CLOSED / HALF_OPEN -> OPEN --

class TestHalfOpenTransitions:
    """Tests the HALF_OPEN state transitions."""

    def test_half_open_success_closes_circuit(self):
        with patch("orchid.hooks.circuit_breaker.time") as time_mock:
            time_mock.time.return_value = 1000.0
            r = CircuitBreakerRegistry()
            r.configure(CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout=5,
                success_threshold=1,
            ))
            r.record_failure("event_a")
            r.record_failure("event_a")
            state = r.get_breaker("event_a")
            with state.lock:
                state.last_state_change = 1000.0
            time_mock.time.return_value = 1006.0
            r.allow_request("event_a")
            r.record_success("event_a")
            state = r.get_breaker("event_a")
            assert state.state == CircuitState.CLOSED
            assert state.failure_count == 0
            assert state.success_count == 0

    def test_half_open_failure_opens_circuit(self):
        with patch("orchid.hooks.circuit_breaker.time") as time_mock:
            time_mock.time.return_value = 1000.0
            r = CircuitBreakerRegistry()
            r.configure(CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout=5,
            ))
            r.record_failure("event_a")
            r.record_failure("event_a")
            state = r.get_breaker("event_a")
            with state.lock:
                state.last_state_change = 1000.0
            time_mock.time.return_value = 1006.0
            r.allow_request("event_a")
            r.record_failure("event_a")
            state = r.get_breaker("event_a")
            assert state.state == CircuitState.OPEN

    def test_half_open_success_threshold_multi(self):
        """Test success_threshold > 1 requires multiple successes."""
        with patch("orchid.hooks.circuit_breaker.time") as time_mock:
            time_mock.time.return_value = 1000.0
            r = CircuitBreakerRegistry()
            r.configure(CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout=5,
                success_threshold=3,
            ))
            r.record_failure("event_a")
            r.record_failure("event_a")
            state = r.get_breaker("event_a")
            with state.lock:
                state.last_state_change = 1000.0
            time_mock.time.return_value = 1006.0
            r.allow_request("event_a")
            r.record_success("event_a")
            state = r.get_breaker("event_a")
            assert state.state == CircuitState.HALF_OPEN
            assert state.success_count == 1
            r.record_success("event_a")
            state = r.get_breaker("event_a")
            assert state.state == CircuitState.HALF_OPEN
            assert state.success_count == 2
            r.record_success("event_a")
            state = r.get_breaker("event_a")
            assert state.state == CircuitState.CLOSED
            assert state.success_count == 0
            assert state.failure_count == 0

    def test_success_in_closed_resets_failure_count(self):
        """Test that success in CLOSED state resets failure_count."""
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=5))
        for _ in range(3):
            r.record_failure("event_a")
        state = r.get_breaker("event_a")
        assert state.failure_count == 3
        r.record_success("event_a")
        assert state.failure_count == 0


# -- allow_request in CLOSED state --

class TestAllowRequestClosed:
    """Tests allow_request when circuit is CLOSED."""

    def test_closed_always_allows(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        for _ in range(20):
            assert r.allow_request("event_a") is True
        state = r.get_breaker("event_a")
        assert state.state == CircuitState.CLOSED


# -- Module-level convenience functions --

class TestModuleLevelFunctions:
    """Tests for module-level convenience functions."""

    def test_configure_circuit_breaker(self):
        configure_circuit_breaker(CircuitBreakerConfig(failure_threshold=10))
        assert get_circuit_breaker_config().failure_threshold == 10

    def test_allow_request_module_level(self):
        configure_circuit_breaker(CircuitBreakerConfig())
        assert allow_request("event_x") is True

    def test_record_success_module_level(self):
        configure_circuit_breaker(CircuitBreakerConfig(failure_threshold=5))
        record_failure("event_x")
        record_success("event_x")
        state = get_circuit_state("event_x")
        assert state["failure_count"] == 0

    def test_record_failure_module_level(self):
        configure_circuit_breaker(CircuitBreakerConfig(failure_threshold=2))
        record_failure("event_x")
        record_failure("event_x")
        state = get_circuit_state("event_x")
        assert state["state"] == "open"

    def test_reset_circuit_breaker_single(self):
        configure_circuit_breaker(CircuitBreakerConfig(failure_threshold=2))
        record_failure("event_x")
        record_failure("event_x")
        assert get_circuit_state("event_x")["state"] == "open"
        reset_circuit_breaker("event_x")
        assert get_circuit_state("event_x")["state"] == "closed"
        assert get_circuit_state("event_x")["failure_count"] == 0

    def test_reset_circuit_breaker_all(self):
        configure_circuit_breaker(CircuitBreakerConfig(failure_threshold=2))
        record_failure("event_a")
        record_failure("event_a")
        record_failure("event_b")
        record_failure("event_b")
        all_s = get_all_circuit_states()
        assert len(all_s) == 2
        reset_circuit_breaker()
        assert len(get_all_circuit_states()) == 0

    def test_get_circuit_state_snapshot(self):
        configure_circuit_breaker(CircuitBreakerConfig(failure_threshold=3))
        record_failure("event_snap")
        record_failure("event_snap")
        snap = get_circuit_state("event_snap")
        assert snap["event_type"] == "event_snap"
        assert snap["state"] == "closed"
        assert snap["failure_count"] == 2
        assert "last_failure_time" in snap
        assert "last_state_change" in snap
        assert "half_open_calls" in snap

    def test_get_all_circuit_states(self):
        configure_circuit_breaker(CircuitBreakerConfig())
        record_failure("evt1")
        record_success("evt2")
        all_states = get_all_circuit_states()
        assert "evt1" in all_states
        assert "evt2" in all_states
        assert all_states["evt1"]["failure_count"] == 1
        assert all_states["evt2"]["failure_count"] == 0

    def test_get_circuit_breaker_config(self):
        configure_circuit_breaker(CircuitBreakerConfig(failure_threshold=7))
        cfg = get_circuit_breaker_config()
        assert cfg.failure_threshold == 7


# -- _get_registry helper --

class TestGetRegistry:
    """Tests for the _get_registry helper."""

    def test_lazy_initialization(self):
        _force_reset_module_state()
        reg = _get_registry()
        assert reg is not None
        assert isinstance(reg, CircuitBreakerRegistry)

    def test_returns_same_instance(self):
        _force_reset_module_state()
        r1 = _get_registry()
        r2 = _get_registry()
        assert r1 is r2


# -- Thread safety --

class TestThreadSafety:
    """Tests for thread-safety of concurrent operations."""

    def test_concurrent_record_failure(self):
        """Multiple threads recording failures simultaneously.

        The circuit opens at failure_threshold=50, so the final
        failure_count will be exactly 50 (not 200) because once
        OPEN, record_failure only updates the timestamp.
        The key assertion is that no exceptions are raised.
        """
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=50))
        errors = []

        def worker():
            try:
                for _ in range(50):
                    r.record_failure("concurrent_event")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        state = r.get_breaker("concurrent_event")
        # Circuit opens at threshold; extra failures only update timestamp
        assert state.state == CircuitState.OPEN
        assert state.failure_count == 50

    def test_concurrent_allow_request_and_record(self):
        """Mixing allow_request with record_failure/record_success."""
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=1000))
        errors = []

        def allow_worker():
            try:
                for _ in range(100):
                    r.allow_request("mixed_event")
            except Exception as e:
                errors.append(e)

        def record_worker():
            try:
                for _ in range(50):
                    r.record_failure("mixed_event")
                    r.record_success("mixed_event")
            except Exception as e:
                errors.append(e)

        threads = []
        threads.append(threading.Thread(target=allow_worker))
        threads.append(threading.Thread(target=record_worker))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# -- Per-event isolation --

class TestEventIsolation:
    """Tests that different event types have independent circuit breakers."""

    def test_failure_in_one_event_does_not_affect_another(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=3))
        r.record_failure("event_a")
        r.record_failure("event_a")
        r.record_failure("event_a")
        assert r.get_breaker("event_a").state == CircuitState.OPEN
        assert r.get_breaker("event_b").state == CircuitState.CLOSED
        assert r.allow_request("event_b") is True

    def test_success_in_one_event_does_not_affect_another(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=5))
        r.record_failure("event_a")
        r.record_failure("event_a")
        r.record_success("event_a")
        state_a = r.get_breaker("event_a")
        assert state_a.failure_count == 0
        state_b = r.get_breaker("event_b")
        assert state_b.failure_count == 0


# -- Full lifecycle test --

class TestFullLifecycle:
    """End-to-end test of the full CLOSED -> OPEN -> HALF_OPEN -> CLOSED cycle."""

    def test_full_cycle(self):
        with patch("orchid.hooks.circuit_breaker.time") as time_mock:
            time_mock.time.return_value = 100.0

            r = CircuitBreakerRegistry()
            r.configure(CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=10,
                success_threshold=1,
            ))

            # Phase 1: CLOSED, accumulate failures
            for _ in range(3):
                r.record_failure("lifecycle")
            state = r.get_breaker("lifecycle")
            assert state.state == CircuitState.OPEN

            # Phase 2: OPEN, requests rejected
            assert r.allow_request("lifecycle") is False

            # Phase 3: OPEN -> HALF_OPEN after timeout
            time_mock.time.return_value = 111.0
            assert r.allow_request("lifecycle") is True
            state = r.get_breaker("lifecycle")
            assert state.state == CircuitState.HALF_OPEN

            # Phase 4: HALF_OPEN -> CLOSED on success
            r.record_success("lifecycle")
            state = r.get_breaker("lifecycle")
            assert state.state == CircuitState.CLOSED
            assert state.failure_count == 0

            # Phase 5: CLOSED, normal operation resumes
            assert r.allow_request("lifecycle") is True
            r.record_failure("lifecycle")
            assert r.get_breaker("lifecycle").state == CircuitState.CLOSED


# -- Configuration edge cases --

class TestConfigEdgeCases:
    """Tests for edge cases in configuration."""

    def test_failure_threshold_one(self):
        """A threshold of 1 means one failure opens the circuit."""
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=1))
        r.record_failure("fast_open")
        assert r.get_breaker("fast_open").state == CircuitState.OPEN

    def test_recovery_timeout_zero(self):
        """Zero recovery timeout means immediate transition to HALF_OPEN."""
        with patch("orchid.hooks.circuit_breaker.time") as time_mock:
            time_mock.time.return_value = 100.0
            r = CircuitBreakerRegistry()
            r.configure(CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0,
            ))
            r.record_failure("zero_timeout")
            assert r.get_breaker("zero_timeout").state == CircuitState.OPEN
            assert r.allow_request("zero_timeout") is True
            assert r.get_breaker("zero_timeout").state == CircuitState.HALF_OPEN

    def test_monitored_events_config(self):
        """Test that monitored_events list is properly set."""
        cfg = CircuitBreakerConfig(monitored_events=["custom1", "custom2"])
        assert cfg.monitored_events == ["custom1", "custom2"]


# -- get_state / get_all_states serialisation --

class TestStateSerialization:
    """Tests that state snapshots are serialisable dicts."""

    def test_get_state_returns_dict(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        snap = r.get_state("serial_test")
        assert isinstance(snap, dict)
        assert "event_type" in snap
        assert "state" in snap
        assert "failure_count" in snap
        assert "success_count" in snap
        assert "last_failure_time" in snap
        assert "last_state_change" in snap
        assert "half_open_calls" in snap

    def test_get_all_states_returns_dict_of_dicts(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        r.record_failure("evt_a")
        r.record_success("evt_b")
        all_s = r.get_all_states()
        assert isinstance(all_s, dict)
        assert "evt_a" in all_s
        assert "evt_b" in all_s
        assert isinstance(all_s["evt_a"], dict)
        assert isinstance(all_s["evt_b"], dict)

    def test_state_json_serialisable(self):
        """Ensure state dict can be passed to json.dumps."""
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=2))
        r.record_failure("json_test")
        r.record_failure("json_test")
        snap = r.get_state("json_test")
        json.dumps(snap)
        all_s = r.get_all_states()
        json.dumps(all_s)


# -- Reset behaviour --

class TestResetBehaviour:
    """Tests for the reset method."""

    def test_reset_individual_breaker(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=2))
        r.record_failure("reset_one")
        r.record_failure("reset_one")
        assert r.get_breaker("reset_one").state == CircuitState.OPEN
        r.reset("reset_one")
        assert r.get_breaker("reset_one").state == CircuitState.CLOSED
        assert r.get_breaker("reset_one").failure_count == 0

    def test_reset_all_breakers(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig(failure_threshold=2))
        r.record_failure("a")
        r.record_failure("a")
        r.record_failure("b")
        r.record_failure("b")
        r.record_failure("c")
        assert len(r._breakers) == 3
        r.reset()
        assert len(r._breakers) == 0

    def test_reset_nonexistent_event_is_noop(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        r.reset("nonexistent_event")
        assert "nonexistent_event" not in r._breakers


# -- _transition internal method --

class TestTransitionInternal:
    """Tests for the internal _transition method."""

    def test_transition_closed_to_open(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        breaker = r.get_breaker("trans_test")
        r._transition(breaker, CircuitState.OPEN)
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 0

    def test_transition_open_to_half_open(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        breaker = r.get_breaker("trans_test")
        breaker.state = CircuitState.OPEN
        r._transition(breaker, CircuitState.HALF_OPEN)
        assert breaker.state == CircuitState.HALF_OPEN
        assert breaker.success_count == 0
        assert breaker.half_open_calls == 0

    def test_transition_half_open_to_closed(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        breaker = r.get_breaker("trans_test")
        breaker.state = CircuitState.HALF_OPEN
        r._transition(breaker, CircuitState.CLOSED)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0
        assert breaker.success_count == 0

    def test_transition_updates_last_state_change(self):
        r = CircuitBreakerRegistry()
        r.configure(CircuitBreakerConfig())
        breaker = r.get_breaker("trans_test")
        old_change = breaker.last_state_change
        _time.sleep(0.01)
        r._transition(breaker, CircuitState.OPEN)
        assert breaker.last_state_change > old_change
