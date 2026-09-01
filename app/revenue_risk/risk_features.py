"""Feature engineering for all ML models (§5.1, §5.6, §6.3, §15.3)."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from app.core.recovery_case import RecoveryCase

if TYPE_CHECKING:
    import pandas as pd


@dataclasses.dataclass(frozen=True)
class RiskFeatures:
    """Feature vector fed to propensity, uplift, and fraud models.

    Includes only the features the ML models may actually see — the hidden
    true_* ground-truth columns from §15.3 must NEVER reach the models at
    inference or training time.
    """

    amount_paise: int
    failure_reason: str
    payment_method: str
    bank: str
    hour_of_day: int
    day_of_week: int
    day_of_month: int
    days_overdue: int
    previous_retry_success: bool
    previous_dunning_response: str
    ptp_history_count: int
    customer_segment: str
    recent_activity_ts: float


class FeatureBuilder:
    """Builds a RiskFeatures vector for a RecoveryCase."""

    def build(self, case: RecoveryCase, raw_event: dict) -> RiskFeatures:
        raise NotImplementedError(
            "TODO: ML track — see implementation_plan.md §5.1, §15.3"
        )

    def to_frame(self, cases: list[RiskFeatures]) -> pd.DataFrame:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.1")
