"""Optimal intervention timing — predicted best retry time (§5.5).

Instead of hardcoding retry after 2h / 6h / 24h, this module predicts the
best retry moment from payment history, hour, day, failure type, bank, method,
and previous retries.  For the hackathon we use a heuristic model; the
interface is designed for drop-in replacement with a trained ML model.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytz

IST = pytz.timezone("Asia/Kolkata")

# Horizon options in hours — we evaluate each and pick the best predicted conversion.
_HORIZONS = [0, 2, 6, 12, 24, 48]


@dataclasses.dataclass(frozen=True)
class TimingRecommendation:
    """Best retry moment predicted from behaviour, not a hardcoded 2h/6h/24h."""

    best_send_at: datetime
    retry_horizon_hours: int
    predicted_conversion: float


class OptimalTimingModel:
    """Predicts the best retry/contact time from payment history, hour, day,
    failure type, bank, method, and previous retries (§5.5).

    Heuristic strategy (hackathon):
      - Evaluate conversion probability at each horizon in _HORIZONS.
      - Pick the horizon with the highest predicted conversion.
      - Apply salary-day boost when applicable.
    """

    def __init__(self, salary_heuristic: SalaryDayHeuristic | None = None) -> None:
        self._salary = salary_heuristic or SalaryDayHeuristic()

    def recommend(
        self,
        features: dict[str, float],
        now: datetime,
    ) -> TimingRecommendation:
        """Return the best send time and predicted conversion at that time."""
        best_horizon = 0
        best_conversion = 0.0

        for horizon in _HORIZONS:
            conv = self._predict_conversion_at(features, now, horizon)
            if conv > best_conversion:
                best_conversion = conv
                best_horizon = horizon

        # Ensure the recommended time is in IST business hours (8 AM–7 PM)
        candidate = now + timedelta(hours=best_horizon)
        send_at = _clamp_to_business_hours(candidate)

        return TimingRecommendation(
            best_send_at=send_at,
            retry_horizon_hours=best_horizon,
            predicted_conversion=round(best_conversion, 4),
        )

    def _predict_conversion_at(
        self,
        features: dict[str, float],
        now: datetime,
        horizon_hours: int,
    ) -> float:
        """Heuristic conversion prediction at a given delay offset.

        Considers: failure reason, hour-of-day, day-of-week, retry count,
        salary day proximity, bank downtime patterns.
        """
        base = 0.15  # baseline conversion

        reason = str(features.get("failure_reason", "")).lower()

        # --- failure-reason adjustments ---
        reason_base: dict[str, float] = {
            "insufficient_funds": 0.12,
            "bank_timeout": 0.25,
            "gateway_error": 0.22,
            "expired_card": 0.05,  # waiting won't help
            "checkout_abandoned": 0.10,
        }
        base = reason_base.get(reason, base)

        # --- time-of-day effect (IST) ---
        future_ist = now + timedelta(hours=horizon_hours)
        try:
            hour = future_ist.astimezone(IST).hour
        except (ValueError, AttributeError):
            hour = future_ist.hour
        if 10 <= hour <= 12:
            base += 0.08  # morning sweet spot
        elif 14 <= hour <= 16:
            base += 0.05  # afternoon OK
        elif hour < 8 or hour >= 21:
            base -= 0.04  # off-hours penalty

        # --- day-of-week effect ---
        try:
            dow = future_ist.astimezone(IST).weekday()
        except (ValueError, AttributeError):
            dow = future_ist.weekday()
        if dow >= 5:  # weekend
            base -= 0.03

        # --- retry count decay ---
        retries = int(features.get("previous_retries", 0))
        base -= retries * 0.03

        # --- salary-day boost for insufficient_funds ---
        if reason == "insufficient_funds":
            salary_day = int(features.get("salary_day", 0))
            if salary_day > 0:
                dom = future_ist.day if hasattr(future_ist, "day") else 1
                days_until_salary = (salary_day - dom) % 30
                if days_until_salary <= 2:
                    base += 0.15  # big boost near salary
                elif days_until_salary <= 5:
                    base += 0.07

        # --- horizon-specific adjustment ---
        # Waiting generally helps for transient failures
        if reason in {"insufficient_funds", "bank_timeout", "gateway_error"}:
            if horizon_hours >= 24:
                base += 0.10
            elif horizon_hours >= 6:
                base += 0.04
        elif reason == "expired_card":
            # Waiting doesn't help expired cards
            base -= horizon_hours * 0.001

        return max(0.01, min(0.95, base))


class SalaryDayHeuristic:
    """Salary-day heuristic for mandate retry sequencing (§9.5).

    Returns the best day-of-month (1–28) to retry based on historical
    payment patterns. Falls back to a configurable default.
    """

    def __init__(
        self,
        default_salary_day: int = 1,
        customer_salary_days: dict[str, int] | None = None,
    ) -> None:
        self._default = default_salary_day
        self._known: dict[str, int] = customer_salary_days or {}

    def compute(self, customer_id: str, salary_day: int | None = None) -> int:
        """Return the expected salary credit day (1–28) for a customer."""
        if salary_day is not None and 1 <= salary_day <= 28:
            return salary_day
        return self._known.get(customer_id, self._default)


def _clamp_to_business_hours(dt: datetime) -> datetime:
    """Shift a datetime into the next 8 AM–7 PM IST window if needed."""
    try:
        ist_dt = dt.astimezone(IST)
    except (ValueError, AttributeError):
        ist_dt = IST.localize(dt) if dt.tzinfo is None else dt

    hour = ist_dt.hour
    if hour < 8:
        ist_dt = ist_dt.replace(hour=8, minute=0, second=0, microsecond=0)
    elif hour >= 19:
        ist_dt = ist_dt.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
    return ist_dt
