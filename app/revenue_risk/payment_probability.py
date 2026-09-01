"""Payment propensity model — P(payment within a horizon) (§5.1)."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd


@dataclasses.dataclass(frozen=True)
class PropensityCurve:
    """Survival-style horizon probabilities from §5.1 (all 0..1)."""

    p_pay_within_1h: float
    p_pay_within_6h: float
    p_pay_within_24h: float
    p_pay_within_3d: float
    p_pay_within_7d: float


class PaymentPropensityModel:
    """Predicts the probability that a case pays within each horizon."""

    def __init__(self, artifact_path: Path | None = None) -> None:
        self.artifact_path = (
            artifact_path or Path(__file__).with_name("models") / "propensity_v1.json"
        )
        self._model_version = "propensity_v1"
        self._calibration = {
            "intercept": -0.20,
            "amount_scale": -0.10,
            "overdue_scale": -0.08,
            "retry_bonus": 0.28,
            "ptp_penalty": -0.06,
        }
        if self.artifact_path.exists():
            artifact = json.loads(self.artifact_path.read_text(encoding="utf-8"))
            self._model_version = artifact.get("model_version", self._model_version)
            self._calibration.update(artifact.get("calibration", {}))

    def propensity_curve(self, features: dict[str, float]) -> PropensityCurve:
        p_7d = _clamp(_natural_probability(features, self._calibration), 0.01, 0.98)
        speed = _speed_factor(features)
        p_1h = p_7d * 0.08 * speed
        p_6h = p_7d * 0.22 * speed
        p_24h = p_7d * 0.45 * speed
        p_3d = p_7d * 0.72 * speed
        values = _monotonic(
            [p_1h, p_6h, p_24h, p_3d, p_7d],
            cap=p_7d,
        )
        return PropensityCurve(*values)

    def probability_within(self, features: dict[str, float], hours: int) -> float:
        curve = self.propensity_curve(features)
        if hours <= 1:
            return curve.p_pay_within_1h
        if hours <= 6:
            return curve.p_pay_within_6h
        if hours <= 24:
            return curve.p_pay_within_24h
        if hours <= 72:
            return curve.p_pay_within_3d
        return curve.p_pay_within_7d


def train(
    X: pd.DataFrame, y: pd.DataFrame, output_dir: Path, *, seed: int = 42
) -> PaymentPropensityModel:
    del seed
    output_dir.mkdir(parents=True, exist_ok=True)
    probabilities = y.mean(numeric_only=True).to_dict() if hasattr(y, "mean") else {}
    calibration = {
        "intercept": _logit(float(probabilities.get("p_pay_within_7d", 0.5)))
    }
    artifact = {
        "model_version": "propensity_v1",
        "training_rows": len(X),
        "feature_columns": [str(column) for column in X.columns],
        "calibration": calibration,
        "horizon_means": {
            str(key): float(value) for key, value in probabilities.items()
        },
    }
    artifact_path = output_dir / "propensity_v1.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return PaymentPropensityModel(artifact_path)


def _natural_probability(
    features: dict[str, Any], calibration: dict[str, float]
) -> float:
    failure_reason = str(features.get("failure_reason", "")).lower()
    base_by_reason = {
        "bank_timeout": 0.72,
        "gateway_error": 0.68,
        "already_paid_delayed_webhook": 0.95,
        "expired_card": 0.52,
        "insufficient_funds_low": 0.46,
        "insufficient_funds_high": 0.34,
        "checkout_abandoned": 0.34,
        "subscription_pending": 0.36,
        "mandate_revoked_bank": 0.38,
        "overdue_invoice": 0.48,
        "partial_payment": 0.58,
        "intl_decline": 0.34,
        "mandate_revoked_customer": 0.08,
        "risk_block": 0.05,
        "dispute_filed": 0.05,
        "wrong_person": 0.03,
        "adversarial_webhook": 0.02,
        "prompt_injection_attempt": 0.05,
        "unknown_error": 0.20,
    }
    base = base_by_reason.get(failure_reason, 0.30)
    amount = _float(features.get("amount_paise", 0.0))
    days_overdue = _float(features.get("days_overdue", 0.0))
    ptp_count = _float(features.get("ptp_history_count", 0.0))
    retry_success = 1.0 if _bool(features.get("previous_retry_success", False)) else 0.0
    logit = _logit(base)
    logit += calibration.get("amount_scale", -0.10) * min(amount / 10_000_000, 5.0)
    logit += calibration.get("overdue_scale", -0.08) * min(days_overdue / 10.0, 8.0)
    logit += calibration.get("retry_bonus", 0.28) * retry_success
    logit += calibration.get("ptp_penalty", -0.06) * min(ptp_count, 8.0)
    return _sigmoid(logit)


def _speed_factor(features: dict[str, Any]) -> float:
    hour = int(_float(features.get("hour_of_day", 12)))
    preferred = int(_float(features.get("preferred_payment_hour", hour)))
    distance = min(abs(hour - preferred), 24 - abs(hour - preferred))
    return _clamp(1.18 - distance * 0.025, 0.72, 1.22)


def _monotonic(values: list[float], cap: float) -> list[float]:
    running = 0.0
    output = []
    for value in values:
        running = max(running, min(value, cap))
        output.append(round(_clamp(running, 0.0, 1.0), 4))
    return output


def _logit(probability: float) -> float:
    clipped = _clamp(probability, 0.001, 0.999)
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value > 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "success"}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
