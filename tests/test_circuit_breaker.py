"""Test suite: Circuit Breaker (§21.3) — opens after N failures, escalates."""

import pytest

from app.executor.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    """§21.3: Circuit breaker opens after failures, escalates."""

    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.allow("razorpay")

    def test_opens_after_threshold(self):
        """§21.3: Mock Razorpay API to fail 5x → circuit breaker opens."""
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(5):
            cb.record_failure("razorpay")
        assert not cb.allow("razorpay")

    def test_stays_closed_under_threshold(self):
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            cb.record_failure("razorpay")
        assert cb.allow("razorpay")

    def test_success_resets(self):
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            cb.record_failure("razorpay")
        cb.record_success("razorpay")
        assert cb.allow("razorpay")

    def test_independent_dependencies(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure("razorpay")
        assert not cb.allow("razorpay")
        assert cb.allow("redis")  # Independent

    def test_state_tracks_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        state = cb.state("razorpay")
        assert state.closed
        assert state.consecutive_failures == 0
        cb.record_failure("razorpay")
        cb.record_failure("razorpay")
        state = cb.state("razorpay")
        assert state.consecutive_failures == 2
