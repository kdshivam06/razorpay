"""Optimal intervention timing — predicted best retry time (§5.5)."""

from __future__ import annotations

import dataclasses
from datetime import datetime


@dataclasses.dataclass(frozen=True)
class TimingRecommendation:
    """Best retry moment predicted from behaviour, not a hardcoded 2h/6h/24h."""

    best_send_at: datetime
    retry_horizon_hours: int
    predicted_conversion: float


class OptimalTimingModel:
    """Predicts the best retry/contact time from payment history, hour, day,
    failure type, bank, method, and previous retries (§5.5)."""

    def recommend(
        self, features: dict[str, float], now: datetime
    ) -> TimingRecommendation:
        raise NotImplementedError(
            "TODO: ML track — see implementation_plan.md §5.5, §9.5"
        )


class SalaryDayHeuristic:
    """Salary-day heuristic for mandate retry sequencing (§9.5)."""

    def compute(self, customer_id: str, salary_day: int | None) -> int:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.5")
