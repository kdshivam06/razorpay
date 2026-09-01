"""Feature engineering for all ML models (§5.1, §5.6, §6.3, §15.3)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
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
        created_at = _epoch_seconds(raw_event.get("created_at"))
        observed_dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
        amount = _int(
            raw_event.get(
                "amount_paise",
                raw_event.get("outstanding_amount_paise", case.total_remaining()),
            )
        )
        return RiskFeatures(
            amount_paise=max(0, amount),
            failure_reason=str(raw_event.get("failure_reason", case.root_cause or "")),
            payment_method=str(raw_event.get("payment_method", "")),
            bank=str(raw_event.get("bank", "")),
            hour_of_day=_int(raw_event.get("hour_of_day", observed_dt.hour)),
            day_of_week=_int(raw_event.get("day_of_week", observed_dt.weekday())),
            day_of_month=_int(raw_event.get("day_of_month", observed_dt.day)),
            days_overdue=max(0, _int(raw_event.get("days_overdue", 0))),
            previous_retry_success=_bool(
                raw_event.get("previous_retry_success", False)
            ),
            previous_dunning_response=str(
                raw_event.get("previous_dunning_response", "")
            ),
            ptp_history_count=max(0, _int(raw_event.get("ptp_history_count", 0))),
            customer_segment=str(raw_event.get("customer_segment", "")),
            recent_activity_ts=float(created_at),
        )

    def to_frame(self, cases: list[RiskFeatures]) -> pd.DataFrame:
        import pandas as pd

        return pd.DataFrame(dataclasses.asdict(case) for case in cases)


def _epoch_seconds(value: object) -> int:
    if value in (None, ""):
        return int(datetime.now(timezone.utc).timestamp())
    if isinstance(value, int | float):
        return int(value)
    text = str(value)
    try:
        return int(text)
    except ValueError:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value > 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "success"}
