"""Hybrid root-cause classifier — rules + ML + fallback (§2.4, §4.1).

Per §2.3 LLM Role Boundaries: the LLM explains the classification the
deterministic layer already made — it NEVER changes the classification itself.
"""

from __future__ import annotations

import dataclasses
import logging

from app.classifier.ml_classifier import ClassificationModel
from app.classifier.rules_engine import RulesEngine
from app.contracts import RootCause
from app.core.recovery_case import RecoveryCase
from app.nlp.gemini_client import JsonLlmClient, try_generate_json
from app.revenue_risk.risk_features import RiskFeatures

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Classification:
    """Canonical classifier output consumed by the whole pipeline."""

    root_cause: RootCause
    confidence: float
    reasoning: str
    sources: tuple[str, ...]
    classifier_version: str


# Prompt template for diagnostic rationale generation
_DIAGNOSTIC_RATIONALE_PROMPT = """
You are a payment failure analyst. Given a payment failure reason and the
deterministic classification, produce a concise 1-2 sentence plain-English
explanation of WHY this failure maps to the given root cause.

Failure reason: {failure_reason}
Decline code: {decline_code}
Deterministic root cause: {root_cause}

Rules:
- Explain the classification in 1-2 sentences max.
- Use plain English, no jargon.
- Do NOT invent new causes or change the classification.
- Focus on the specific signal in the failure reason that led to this root cause.
- Output ONLY a JSON object: {{"rationale": "your explanation"}}
""".strip()


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
        llm_client: JsonLlmClient | None = None,
    ) -> None:
        self.rules = rules or RulesEngine()
        self.ml = ml
        self._llm = llm_client

    def classify(
        self, case: RecoveryCase, features: RiskFeatures, event: dict
    ) -> Classification:
        verdict = self.rules.classify_with_rules(case, event)
        if verdict.applies and verdict.root_cause is not None:
            classification = Classification(
                root_cause=verdict.root_cause,
                confidence=0.99 if verdict.priority >= 90 else 0.92,
                reasoning=verdict.reason,
                sources=("rules", verdict.name),
                classifier_version="rules_v1",
            )
            self._maybe_generate_rationale(case, event, classification)
            return classification

        if self.ml is not None:
            probabilities = self.ml.predict_proba(dataclasses.asdict(features))
            root_cause = max(probabilities, key=probabilities.get)
            confidence = probabilities[root_cause]
            if confidence >= 0.45:
                classification = Classification(
                    root_cause=root_cause,
                    confidence=confidence,
                    reasoning="ML fallback selected highest-probability root cause.",
                    sources=("ml",),
                    classifier_version=self.ml.model_version,
                )
                self._maybe_generate_rationale(case, event, classification)
                return classification

        classification = Classification(
            root_cause=RootCause.UNKNOWN_ERROR,
            confidence=0.25,
            reasoning="No decisive rule and ML was unavailable or low-confidence.",
            sources=("deterministic_fallback",),
            classifier_version="fallback_v1",
        )
        self._maybe_generate_rationale(case, event, classification)
        return classification

    def _maybe_generate_rationale(
        self, case: RecoveryCase, event: dict, classification: Classification
    ) -> None:
        """Generate and store LLM diagnostic rationale on the case record.

        Called once per case after classification. LLM only explains the
        deterministic decision — never changes it (§2.3).
        """
        if not self._llm:
            return

        # Only generate if not already present
        if case.diagnostic_rationale:
            return

        failure_reason = event.get("failure_reason", "") or case.root_cause
        decline_code = event.get("decline_code", "") or "N/A"
        root_cause = classification.root_cause.value if hasattr(classification.root_cause, "value") else str(classification.root_cause)

        prompt = _DIAGNOSTIC_RATIONALE_PROMPT.format(
            failure_reason=failure_reason,
            decline_code=decline_code,
            root_cause=root_cause,
        )

        result = try_generate_json(self._llm, prompt)
        rationale = result.get("rationale", "") if result else ""

        if rationale:
            case.diagnostic_rationale = rationale
            logger.debug("Generated diagnostic rationale for %s: %s", case.case_id, rationale)

    def classify_batch(
        self, cases: list[tuple[RecoveryCase, RiskFeatures, dict]]
    ) -> list[Classification]:
        return [self.classify(case, features, event) for case, features, event in cases]
