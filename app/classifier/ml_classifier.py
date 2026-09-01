"""ML root-cause classifier — LightGBM/XGBoost wrapper (§2.4, §5, §9.3)."""

from __future__ import annotations

import base64
import json
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.classifier.rules_engine import FAILURE_REASON_TO_ROOT_CAUSE
from app.contracts import RootCause

if TYPE_CHECKING:
    import pandas as pd


class ClassificationModel:
    """Wraps an XGBoost/LightGBM classifier producing a RootCause distribution.

    The ML layer sits BELOW the deterministic rules in the fallback hierarchy
    (§2.4): its output is used only when rules do not apply with high
    confidence.
    """

    def __init__(self, artifact_path: Path | None = None) -> None:
        self.artifact_path = (
            artifact_path or Path(__file__).with_name("models") / "classifier_v1.json"
        )
        self._model_version = "classifier_v1"
        self._booster = "heuristic"
        self._feature_columns: list[str] = []
        self._vectorizer: Any | None = None
        self._label_encoder: Any | None = None
        self._estimator: Any | None = None

        if self.artifact_path.exists():
            artifact = json.loads(self.artifact_path.read_text(encoding="utf-8"))
            self._model_version = artifact.get("model_version", self._model_version)
            self._booster = artifact.get("booster", self._booster)
            self._feature_columns = list(artifact.get("feature_columns", []))
            model_blob = artifact.get("model_blob_b64")
            if model_blob:
                payload = pickle.loads(base64.b64decode(model_blob.encode("ascii")))
                self._vectorizer = payload["vectorizer"]
                self._label_encoder = payload["label_encoder"]
                self._estimator = payload["estimator"]

    def predict_proba(self, features: dict[str, Any]) -> dict[RootCause, float]:
        clean = _clean_feature_record(features, self._feature_columns)
        failure_reason = str(clean.get("failure_reason", "")).lower()
        if failure_reason in FAILURE_REASON_TO_ROOT_CAUSE:
            return _heuristic_distribution(clean)

        if self._estimator is None or self._vectorizer is None:
            return _heuristic_distribution(clean)

        encoded = self._vectorizer.transform([clean])
        probabilities = self._estimator.predict_proba(encoded)[0]
        encoded_classes = getattr(
            self._estimator, "classes_", range(len(probabilities))
        )
        labels = self._label_encoder.inverse_transform(encoded_classes)

        distribution = {root_cause: 0.0 for root_cause in RootCause}
        for label, probability in zip(labels, probabilities, strict=False):
            distribution[RootCause(str(label))] = float(probability)
        return _normalize_distribution(distribution)

    def predict(self, features: dict[str, Any]) -> RootCause:
        probabilities = self.predict_proba(features)
        return max(probabilities, key=probabilities.get)

    @property
    def model_version(self) -> str:
        return self._model_version


def train(
    X: pd.DataFrame,
    y: pd.Series,
    output_dir: Path,
    *,
    booster: str = "lightgbm",
    seed: int = 42,
) -> ClassificationModel:
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.preprocessing import LabelEncoder

    feature_columns = [
        column
        for column in X.columns
        if not str(column).startswith("true_") and column != "ground_truth"
    ]
    records = [
        _clean_feature_record(record, feature_columns)
        for record in X[feature_columns].to_dict(orient="records")
    ]
    vectorizer = DictVectorizer(sparse=True)
    encoded_x = vectorizer.fit_transform(records)

    label_encoder = LabelEncoder()
    encoded_y = label_encoder.fit_transform(y.astype(str))

    selected_booster, estimator = _build_estimator(booster, seed)
    estimator.fit(encoded_x, encoded_y)

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "classifier_v1.json"
    payload = {
        "vectorizer": vectorizer,
        "label_encoder": label_encoder,
        "estimator": estimator,
    }
    artifact = {
        "model_version": "classifier_v1",
        "booster": selected_booster,
        "requested_booster": booster,
        "feature_columns": feature_columns,
        "classes": [str(label) for label in label_encoder.classes_],
        "training_rows": len(records),
        "model_blob_b64": base64.b64encode(pickle.dumps(payload)).decode("ascii"),
    }
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    return ClassificationModel(artifact_path)


def _build_estimator(booster: str, seed: int) -> tuple[str, Any]:
    requested = booster.lower()
    if requested == "lightgbm":
        try:
            from lightgbm import LGBMClassifier

            return (
                "lightgbm",
                LGBMClassifier(
                    n_estimators=80,
                    learning_rate=0.08,
                    max_depth=5,
                    random_state=seed,
                    verbosity=-1,
                ),
            )
        except ImportError:
            requested = "xgboost"

    if requested == "xgboost":
        try:
            from xgboost import XGBClassifier

            return (
                "xgboost",
                XGBClassifier(
                    n_estimators=80,
                    max_depth=4,
                    learning_rate=0.08,
                    subsample=0.9,
                    colsample_bytree=0.9,
                    objective="multi:softprob",
                    eval_metric="mlogloss",
                    random_state=seed,
                    n_jobs=1,
                ),
            )
        except ImportError:
            pass

    from sklearn.ensemble import RandomForestClassifier

    return (
        "sklearn_random_forest",
        RandomForestClassifier(n_estimators=120, random_state=seed, n_jobs=1),
    )


def _clean_feature_record(
    features: dict[str, Any], feature_columns: list[str] | None = None
) -> dict[str, Any]:
    source = dict(features)
    columns = feature_columns or [
        key
        for key in source
        if not str(key).startswith("true_") and key != "ground_truth"
    ]
    clean: dict[str, Any] = {}
    for column in columns:
        value = source.get(column, "")
        if value is None:
            clean[column] = ""
        elif isinstance(value, bool | int | float | str):
            clean[column] = value
        else:
            clean[column] = str(value)
    return clean


def _heuristic_distribution(features: dict[str, Any]) -> dict[RootCause, float]:
    failure_reason = str(features.get("failure_reason", "")).lower()
    predicted = FAILURE_REASON_TO_ROOT_CAUSE.get(
        failure_reason, RootCause.UNKNOWN_ERROR
    )
    distribution = {root_cause: 0.01 for root_cause in RootCause}
    distribution[predicted] = 0.92
    return _normalize_distribution(distribution)


def _normalize_distribution(
    distribution: dict[RootCause, float],
) -> dict[RootCause, float]:
    total = sum(max(0.0, value) for value in distribution.values())
    if total <= 0:
        return {root_cause: 1.0 / len(RootCause) for root_cause in RootCause}
    return {
        root_cause: max(0.0, probability) / total
        for root_cause, probability in distribution.items()
    }
