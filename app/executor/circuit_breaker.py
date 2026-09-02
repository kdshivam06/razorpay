"""Circuit breaker pattern (§16.1, §9.1).

A degraded/unhealthy dependency trips the breaker; financial write actions
are refused while it is open (§16.1).
"""

from __future__ import annotations

import dataclasses
import logging
import time

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CircuitState:
    """Current circuit position for one dependency."""

    closed: bool
    open_until_ts: float | None
    consecutive_failures: int


class CircuitBreaker:
    """Fail-closed protection per dependency (Razorpay, SMS, Redis, DB)."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: int = 60,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout_s
        self._states: dict[str, dict] = {}

    def _get_state(self, dependency: str) -> dict:
        if dependency not in self._states:
            self._states[dependency] = {
                "closed": True,
                "open_until_ts": None,
                "consecutive_failures": 0,
            }
        return self._states[dependency]

    def allow(self, dependency: str) -> bool:
        """Check if calls to the dependency are allowed."""
        state = self._get_state(dependency)
        if state["closed"]:
            return True

        if state["open_until_ts"] and time.time() >= state["open_until_ts"]:
            # Half-open: allow one trial request
            logger.info("Circuit breaker for %s entering half-open state", dependency)
            return True

        return False

    def record_success(self, dependency: str) -> None:
        """Record a successful call."""
        state = self._get_state(dependency)
        if not state["closed"] or state["consecutive_failures"] > 0:
            logger.info("Circuit breaker for %s closed (recovered)", dependency)
            state["closed"] = True
            state["open_until_ts"] = None
            state["consecutive_failures"] = 0

    def record_failure(self, dependency: str) -> None:
        """Record a failed call."""
        state = self._get_state(dependency)
        state["consecutive_failures"] += 1

        if state["closed"] and state["consecutive_failures"] >= self._failure_threshold:
            logger.warning(
                "Circuit breaker for %s OPENED after %d failures",
                dependency,
                state["consecutive_failures"],
            )
            state["closed"] = False

        if not state["closed"]:
            state["open_until_ts"] = time.time() + self._recovery_timeout

    def state(self, dependency: str) -> CircuitState:
        """Get the current state of a dependency."""
        s = self._get_state(dependency)
        return CircuitState(
            closed=s["closed"],
            open_until_ts=s["open_until_ts"],
            consecutive_failures=s["consecutive_failures"],
        )
