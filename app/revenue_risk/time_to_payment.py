"""Time-to-payment model — expected days (and median) to payment (§5.2)."""

from __future__ import annotations

import base64
import json
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd


class TimeToPaymentModel:
    """Predicts WHEN a case pays, not just whether (§5.2)."""

    def __init__(self, artifact_path: Path | None = None) -> None:
        self.artifact_path = (
            artifact_path
            or Path(__file__).with_name("models") / "time_to_payment_v1.json"
        )
        self._model_version = "time_to_payment_v1"
        self._feature_columns: list[str] = []
        self._vectorizer: Any | None = None
        self._estimator: Any | None = None
        if self.artifact_path.exists():
            artifact = json.loads(self.artifact_path.read_text(encoding="utf-8"))
            self._model_version = artifact.get("model_version", self._model_version)
            self._feature_columns = list(artifact.get("feature_columns", []))
            model_blob = artifact.get("model_blob_b64")
            if model_blob:
                payload = pickle.loads(base64.b64decode(model_blob.encode("ascii")))
                self._vectorizer = payload["vectorizer"]
                self._estimator = payload["estimator"]

    def expected_days_to_payment(self, features: dict[str, float]) -> float:
        if self._estimator is not None and self._vectorizer is not None:
            clean = _clean(features, self._feature_columns)
            predicted = float(
                self._estimator.predict(self._vectorizer.transform([clean]))[0]
            )
            return round(_clamp(predicted, 0.0, 90.0), 2)
        return round(_heuristic_days(features), 2)

    def median_days_to_payment(self, features: dict[str, float]) -> float:
        return round(self.expected_days_to_payment(features) * 0.82, 2)


def train(
    X: pd.DataFrame, y: pd.Series, output_dir: Path, *, seed: int = 42
) -> TimeToPaymentModel:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.feature_extraction import DictVectorizer

    feature_columns = [
        column for column in X.columns if not str(column).startswith("true_")
    ]
    records = [
        _clean(record, feature_columns)
        for record in X[feature_columns].to_dict("records")
    ]
    vectorizer = DictVectorizer(sparse=True)
    encoded_x = vectorizer.fit_transform(records)
    estimator = RandomForestRegressor(n_estimators=120, random_state=seed, n_jobs=1)
    estimator.fit(encoded_x, y.astype(float))

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "time_to_payment_v1.json"
    payload = {"vectorizer": vectorizer, "estimator": estimator}
    artifact = {
        "model_version": "time_to_payment_v1",
        "training_rows": len(records),
        "feature_columns": [str(column) for column in feature_columns],
        "model_blob_b64": base64.b64encode(pickle.dumps(payload)).decode("ascii"),
    }
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return TimeToPaymentModel(artifact_path)


def _heuristic_days(features: dict[str, Any]) -> float:
    reason = str(features.get("failure_reason", "")).lower()
    base_by_reason = {
        "already_paid_delayed_webhook": 0.1,
        "bank_timeout": 1.2,
        "gateway_error": 1.6,
        "expired_card": 4.5,
        "insufficient_funds_low": 5.5,
        "partial_payment": 8.0,
        "checkout_abandoned": 7.0,
        "subscription_pending": 9.0,
        "mandate_revoked_bank": 10.0,
        "intl_decline": 12.0,
        "overdue_invoice": 18.0,
        "insufficient_funds_high": 20.0,
        "unknown_error": 24.0,
        "mandate_revoked_customer": 45.0,
        "risk_block": 60.0,
        "dispute_filed": 60.0,
        "wrong_person": 90.0,
        "adversarial_webhook": 90.0,
        "prompt_injection_attempt": 75.0,
    }
    days = base_by_reason.get(reason, 18.0)
    days += min(_float(features.get("days_overdue", 0.0)) * 0.15, 12.0)
    days += min(_float(features.get("amount_paise", 0.0)) / 10_000_000, 8.0)
    if _bool(features.get("previous_retry_success", False)):
        days *= 0.78
    days += min(_float(features.get("ptp_history_count", 0.0)) * 0.75, 7.0)
    return _clamp(days, 0.0, 90.0)


def _clean(features: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {str(column): features.get(column, "") for column in columns}


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
