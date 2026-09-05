"""Incremental uplift model — per-action uplift over NO_ACTION (§5.3)."""

from __future__ import annotations

import base64
import dataclasses
import json
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.contracts import Action
from app.core.recovery_case import UpliftSegment
from app.revenue_risk.payment_probability import PaymentPropensityModel

if TYPE_CHECKING:
    import pandas as pd


@dataclasses.dataclass(frozen=True)
class UpliftEstimates:
    """P(pay | each action) and the uplift vs NO_ACTION (§5.3, §6.2)."""

    baseline_natural_probability: float
    per_action_probability: dict[Action, float]
    per_action_uplift: dict[Action, float]
    uplift_segment: UpliftSegment | str = ""


class IncrementalUpliftModel:
    """The ★ biggest AI differentiator (§5.3): heterogeneous treatment effects.

    Avoids spending intervention cost on customers who would have paid anyway.
    """

    def __init__(self, artifact_path: Path | None = None) -> None:
        self.artifact_path = (
            artifact_path or Path(__file__).with_name("models") / "uplift_v1.json"
        )
        self._model_version = "uplift_v1"
        self._feature_columns: list[str] = []
        self._vectorizer: Any | None = None
        self._estimator: Any | None = None
        self._propensity = PaymentPropensityModel()
        if self.artifact_path.exists():
            artifact = json.loads(self.artifact_path.read_text(encoding="utf-8"))
            self._model_version = artifact.get("model_version", self._model_version)
            self._feature_columns = list(artifact.get("feature_columns", []))
            model_blob = artifact.get("model_blob_b64")
            if model_blob:
                payload = pickle.loads(base64.b64decode(model_blob.encode("ascii")))
                self._vectorizer = payload["vectorizer"]
                self._estimator = payload["estimator"]

    def uplift_for(self, features: dict[str, float], action: Action) -> float:
        return self.estimates(features).per_action_uplift[action]

    def estimates(self, features: dict[str, float]) -> UpliftEstimates:
        baseline = self._baseline_probability(features)
        probabilities = {
            action: self._probability_for_action(features, action, baseline)
            for action in Action
        }
        probabilities[Action.NO_ACTION] = baseline
        uplift = {
            action: round(probability - baseline, 4)
            for action, probability in probabilities.items()
        }
        segment = segment_from_estimates(baseline, uplift)
        if segment in {UpliftSegment.SURE_THING, UpliftSegment.SLEEPING_DOG}:
            probabilities[Action.NO_ACTION] = baseline
            uplift[Action.NO_ACTION] = 0.0
        return UpliftEstimates(
            baseline_natural_probability=round(baseline, 4),
            per_action_probability=probabilities,
            per_action_uplift=uplift,
            uplift_segment=segment,
        )

    def best_action(self, features: dict[str, float]) -> Action:
        estimates = self.estimates(features)
        if estimates.uplift_segment in {
            UpliftSegment.SURE_THING,
            UpliftSegment.SLEEPING_DOG,
        }:
            return Action.NO_ACTION
        best = max(estimates.per_action_uplift, key=estimates.per_action_uplift.get)
        if estimates.per_action_uplift[best] <= 0:
            return Action.NO_ACTION
        return best

    def _baseline_probability(self, features: dict[str, Any]) -> float:
        return _clamp(self._propensity.probability_within(features, 168), 0.01, 0.98)

    def _probability_for_action(
        self,
        features: dict[str, Any],
        action: Action,
        baseline: float,
    ) -> float:
        if action is Action.NO_ACTION:
            return baseline
        heuristic_uplift = _heuristic_uplift(features, action)
        heuristic_probability = round(
            _clamp(baseline + heuristic_uplift, 0.0, 1.0), 4
        )
        if heuristic_uplift < -0.025:
            return heuristic_probability
        if self._estimator is not None and self._vectorizer is not None:
            clean = _with_treatment(_clean(features, self._feature_columns), action)
            try:
                probability = float(
                    self._estimator.predict_proba(self._vectorizer.transform([clean]))[
                        0
                    ][1]
                )
                model_probability = round(_clamp(probability, 0.0, 1.0), 4)
                model_uplift = model_probability - baseline
                if heuristic_uplift >= 0.08:
                    return max(model_probability, heuristic_probability)
                if abs(model_uplift) > 0.30 and heuristic_uplift < 0.08:
                    return heuristic_probability
                return model_probability
            except (IndexError, ValueError):
                pass
        return heuristic_probability


def train(
    X: pd.DataFrame,
    treatment: pd.Series,
    outcome: pd.Series,
    output_dir: Path,
    *,
    seed: int = 42,
) -> IncrementalUpliftModel:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.feature_extraction import DictVectorizer

    feature_columns = [
        column for column in X.columns if not str(column).startswith("true_")
    ]
    records = []
    for record, action in zip(
        X[feature_columns].to_dict(orient="records"), treatment.astype(str), strict=True
    ):
        records.append(_with_treatment(record, _coerce_action(action)))

    vectorizer = DictVectorizer(sparse=True)
    encoded_x = vectorizer.fit_transform(records)
    estimator = GradientBoostingClassifier(random_state=seed)
    estimator.fit(encoded_x, outcome.astype(int))

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "uplift_v1.json"
    payload = {"vectorizer": vectorizer, "estimator": estimator}
    artifact = {
        "model_version": "uplift_v1",
        "approach": "single outcome model with treatment-interaction features",
        "training_rows": len(records),
        "feature_columns": [str(column) for column in feature_columns],
        "actions": [action.value for action in Action],
        "model_blob_b64": base64.b64encode(pickle.dumps(payload)).decode("ascii"),
    }
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return IncrementalUpliftModel(artifact_path)


def segment_from_estimates(
    baseline_natural_probability: float, per_action_uplift: dict[Action, float]
) -> UpliftSegment:
    action_uplifts = {
        action: uplift
        for action, uplift in per_action_uplift.items()
        if action is not Action.NO_ACTION and action is not Action.BLOCK
    }
    max_uplift = max(action_uplifts.values(), default=0.0)
    min_uplift = min(action_uplifts.values(), default=0.0)
    if baseline_natural_probability >= 0.70 and max_uplift < 0.08:
        return UpliftSegment.SURE_THING
    if min_uplift < -0.025 and max_uplift <= 0.06:
        return UpliftSegment.SLEEPING_DOG
    if max_uplift >= 0.08:
        return UpliftSegment.PERSUADABLE
    return UpliftSegment.LOST_CAUSE


def _heuristic_uplift(features: dict[str, Any], action: Action) -> float:
    reason = str(features.get("failure_reason", "")).lower()
    if action is Action.BLOCK:
        return 0.0
    if reason in {
        "risk_block",
        "dispute_filed",
        "wrong_person",
        "mandate_revoked_customer",
    }:
        harmful = {
            Action.VOICE_CALL: -0.20,
            Action.SEND_SMS: -0.10,
            Action.SEND_WHATSAPP: -0.11,
            Action.SEND_EMAIL: -0.06,
            Action.SEND_PAYMENT_LINK: -0.08,
        }
        return harmful.get(action, -0.05)
    base_by_reason = {
        "expired_card": 0.09,
        "checkout_abandoned": 0.08,
        "insufficient_funds_low": 0.06,
        "insufficient_funds_high": 0.05,
        "subscription_pending": 0.06,
        "mandate_revoked_bank": 0.08,
        "partial_payment": 0.07,
        "overdue_invoice": 0.04,
        "intl_decline": 0.05,
        "unknown_error": 0.01,
        "bank_timeout": 0.01,
        "gateway_error": 0.01,
        "already_paid_delayed_webhook": -0.01,
    }
    uplift = base_by_reason.get(reason, 0.02)
    uplift += _action_bonus(reason, action)
    preferred = str(features.get("channel_preference", "")).upper()
    if _action_matches_channel(action, preferred):
        uplift += 0.045
    if str(features.get("persona", "")) == "P8" and action is Action.VOICE_CALL:
        uplift -= 0.18
    return _clamp(uplift, -0.30, 0.40)


def _action_bonus(reason: str, action: Action) -> float:
    bonuses: dict[str, dict[Action, float]] = {
        "expired_card": {
            Action.REQUEST_PAYMENT_METHOD_UPDATE: 0.18,
            Action.SEND_PAYMENT_LINK: 0.08,
            Action.RETRY_SAME_METHOD: -0.11,
        },
        "checkout_abandoned": {
            Action.SEND_PAYMENT_LINK: 0.11,
            Action.SEND_WHATSAPP: 0.08,
            Action.SEND_SMS: 0.05,
        },
        "insufficient_funds_low": {
            Action.WAIT: 0.07,
            Action.SEND_WHATSAPP: 0.05,
            Action.SEND_SMS: 0.04,
        },
        "insufficient_funds_high": {
            Action.CREATE_PTP: 0.07,
            Action.HUMAN_ESCALATION: 0.07,
        },
        "bank_timeout": {
            Action.WAIT: 0.03,
            Action.RETRY_SAME_METHOD: 0.02,
            Action.VOICE_CALL: -0.06,
        },
        "gateway_error": {
            Action.WAIT: 0.03,
            Action.RETRY_SAME_METHOD: 0.02,
        },
        "mandate_revoked_bank": {
            Action.REQUEST_PAYMENT_METHOD_UPDATE: 0.10,
            Action.SEND_PAYMENT_LINK: 0.11,
        },
        "subscription_pending": {
            Action.SEND_PAYMENT_LINK: 0.08,
            Action.RETRY_ALTERNATE_METHOD: 0.07,
        },
        "partial_payment": {
            Action.OFFER_PARTIAL_PAYMENT: 0.16,
            Action.CREATE_PTP: 0.08,
        },
        "overdue_invoice": {
            Action.CREATE_PTP: 0.08,
            Action.SEND_EMAIL: 0.04,
            Action.HUMAN_ESCALATION: 0.05,
        },
        "intl_decline": {
            Action.RETRY_ALTERNATE_METHOD: 0.09,
            Action.SEND_PAYMENT_LINK: 0.05,
        },
        "already_paid_delayed_webhook": {
            Action.VOICE_CALL: -0.14,
            Action.SEND_SMS: -0.06,
        },
    }
    return bonuses.get(reason, {}).get(action, 0.0)


def _with_treatment(features: dict[str, Any], action: Action | str) -> dict[str, Any]:
    action_value = action.value if isinstance(action, Action) else str(action)
    clean = _clean(features, list(features))
    clean["treatment_action"] = action_value
    for key, value in list(clean.items()):
        clean[f"{key}__x__{action_value}"] = value
    return clean


def _coerce_action(action: str) -> Action | str:
    try:
        return Action(action)
    except ValueError:
        return action


def _clean(features: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {
        str(column): features.get(column, "")
        for column in columns
        if not str(column).startswith("true_")
    }


def _action_matches_channel(action: Action, channel: str) -> bool:
    return (
        (action is Action.SEND_SMS and channel == "SMS")
        or (action is Action.SEND_EMAIL and channel == "EMAIL")
        or (action is Action.SEND_WHATSAPP and channel == "WHATSAPP")
        or (action is Action.VOICE_CALL and channel == "VOICE_CALL")
        or (action is Action.SEND_PAYMENT_LINK and channel == "PAYMENT_LINK")
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
