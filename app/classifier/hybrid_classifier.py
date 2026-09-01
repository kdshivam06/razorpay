"""Hybrid root-cause classifier — rules + ML + fallback (§2.4, §4.1)."""

from __future__ import annotations

import dataclasses

from app.classifier.ml_classifier import ClassificationModel
from app.classifier.rules_engine import RulesEngine
from app.contracts import RootCause
from app.core.recovery_case import RecoveryCase
from app.revenue_risk.risk_features import RiskFeatures


@dataclasses.dataclass(frozen=True)
class Classification:
    """Canonical classifier output consumed by the whole pipeline."""

    root_cause: RootCause
    confidence: float
    reasoning: str
    sources: tuple[str, ...]
    classifier_version: str


class HybridClassifier:
    """Resolves a RecoveryCase to a Classification by combining:

    1. deterministic rules (mandate direction, terminal-vs-transient, halts)
    2. ML probabilities (only when rules are not decisive)
    3. UNKNOWN_ERROR fallback when everything is low-confidence
    """

    def __init__(
        self,
        rules: RulesEngine | None = None,
        ml: ClassificationModel | None = None,
    ) -> None:
        self.rules = rules or RulesEngine()
        self.ml = ml

    def classify(
        self, case: RecoveryCase, features: RiskFeatures, event: dict
    ) -> Classification:
        verdict = self.rules.classify_with_rules(case, event)
        if verdict.applies and verdict.root_cause is not None:
            return Classification(
                root_cause=verdict.root_cause,
                confidence=0.99 if verdict.priority >= 90 else 0.92,
                reasoning=verdict.reason,
                sources=("rules", verdict.name),
                classifier_version="rules_v1",
            )

        if self.ml is not None:
            probabilities = self.ml.predict_proba(dataclasses.asdict(features))
            root_cause = max(probabilities, key=probabilities.get)
            confidence = probabilities[root_cause]
            if confidence >= 0.45:
                return Classification(
                    root_cause=root_cause,
                    confidence=confidence,
                    reasoning="ML fallback selected highest-probability root cause.",
                    sources=("ml",),
                    classifier_version=self.ml.model_version,
                )

        return Classification(
            root_cause=RootCause.UNKNOWN_ERROR,
            confidence=0.25,
            reasoning="No decisive rule and ML was unavailable or low-confidence.",
            sources=("deterministic_fallback",),
            classifier_version="fallback_v1",
        )

    def classify_batch(
        self, cases: list[tuple[RecoveryCase, RiskFeatures, dict]]
    ) -> list[Classification]:
        return [self.classify(case, features, event) for case, features, event in cases]
