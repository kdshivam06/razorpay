"""Circuit breaker pattern (§16.1, §9.1)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class CircuitState:
    """Current circuit position for one dependency."""

    closed: bool
    open_until_ts: float | None
    consecutive_failures: int


class CircuitBreaker:
    """Fail-closed protection per dependency (Razorpay, SMS, Redis, DB).

    A degraded/unhealthy dependency trips the breaker; financial write actions
    are refused while it is open (§16.1)."""

    def allow(self, dependency: str) -> bool:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §16.1"
        )

    def record_success(self, dependency: str) -> None:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §16.1"
        )

    def record_failure(self, dependency: str) -> None:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §16.1"
        )

    def state(self, dependency: str) -> CircuitState:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §16.1"
        )
