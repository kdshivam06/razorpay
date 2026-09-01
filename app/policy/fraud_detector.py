"""Fraud pattern detection (§5.6, §7.1, §7.7)."""

from __future__ import annotations

import dataclasses

from app.contracts import RiskLevel


@dataclasses.dataclass(frozen=True)
class FraudVerdict:
    """A fraud assessment for one case."""

    fraud_score: float
    risk_level: RiskLevel
    flagged: bool
    reasons: tuple[str, ...]


class FraudDetector:
    """Scores fraud risk and BLOCKS outbound recovery when flagged.

    A fraud block is a first-class stopping rule (§6.9), and the amount and
    fraud risk must gate autonomous action separately from model confidence
    (§7.7)."""

    def assess(self, features: dict[str, float]) -> FraudVerdict:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.1, §7.7")

    def assert_not_fraudulent(self, fraud_score: float, threshold: float) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.1")