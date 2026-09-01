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
        raise NotImplementedError(
            "TODO: ML track — see implementation_plan.md §2.4, §4.1"
        )

    def classify(
        self, case: RecoveryCase, features: RiskFeatures, event: dict
    ) -> Classification:
        raise NotImplementedError(
            "TODO: ML track — see implementation_plan.md §2.4, §4.1"
        )

    def classify_batch(
        self, cases: list[tuple[RecoveryCase, RiskFeatures, dict]]
    ) -> list[Classification]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §2.4")
