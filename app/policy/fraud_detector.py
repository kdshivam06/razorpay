"""Fraud pattern detection (§5.6, §7.1, §7.7).

Fraud is a first-class stopping rule (§6.9).  The fraud score and risk level
gate autonomous action SEPARATELY from model confidence (§7.7).

FAILS CLOSED: if fraud assessment cannot be computed, action is BLOCKED.
"""

from __future__ import annotations

import dataclasses

from app.contracts import Action, RiskLevel


@dataclasses.dataclass(frozen=True)
class FraudVerdict:
    """A fraud assessment for one case."""

    fraud_score: float
    risk_level: RiskLevel
    flagged: bool
    reasons: tuple[str, ...]


# Threshold configuration
_FRAUD_THRESHOLDS = {
    "critical": 0.80,  # ≥ 0.80 → CRITICAL, immediate block
    "high": 0.55,  # ≥ 0.55 → HIGH, requires human approval
    "medium": 0.30,  # ≥ 0.30 → MEDIUM, limited autonomous action
    "low": 0.0,  # < 0.30 → LOW, full autonomy
}

# Actions that are ALWAYS blocked when fraud is HIGH or CRITICAL
_FRAUD_BLOCKED_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.RETRY_SAME_METHOD,
        Action.RETRY_ALTERNATE_METHOD,
        Action.SEND_PAYMENT_LINK,
        Action.OFFER_PARTIAL_PAYMENT,
    }
)


class FraudDetector:
    """Scores fraud risk and BLOCKS outbound recovery when flagged.

    A fraud block is a first-class stopping rule (§6.9), and the amount and
    fraud risk must gate autonomous action separately from model confidence
    (§7.7).
    """

    def __init__(
        self,
        thresholds: dict[str, float] | None = None,
    ) -> None:
        self._t = dict(_FRAUD_THRESHOLDS)
        if thresholds:
            self._t.update(thresholds)

    def assess(self, features: dict[str, float]) -> FraudVerdict:
        """Score fraud risk from case features.

        Expected features:
            fraud_score: float (0..1) — from Razorpay's risk engine
            dispute_count: int — number of disputes for this customer
            chargeback_count: int — number of chargebacks
            velocity_score: float — unusual transaction velocity
            identity_mismatch: bool — name/phone don't match

        FAILS CLOSED: missing fraud_score → treat as HIGH risk.
        """
        fraud_score = features.get("fraud_score")
        if fraud_score is None:
            # FAIL CLOSED: no fraud data → HIGH risk
            return FraudVerdict(
                fraud_score=0.55,
                risk_level=RiskLevel.HIGH,
                flagged=True,
                reasons=("fraud_score unavailable — fail closed",),
            )

        fraud_score = max(0.0, min(1.0, float(fraud_score)))
        reasons: list[str] = []

        # Enrich with other signals
        dispute_count = int(features.get("dispute_count", 0))
        chargeback_count = int(features.get("chargeback_count", 0))
        velocity = float(features.get("velocity_score", 0.0))
        identity_mismatch = bool(features.get("identity_mismatch", False))

        # Boost fraud score based on supporting signals
        effective_score = fraud_score

        if dispute_count >= 3:
            effective_score += 0.15
            reasons.append(f"{dispute_count} disputes on record")

        if chargeback_count >= 2:
            effective_score += 0.20
            reasons.append(f"{chargeback_count} chargebacks on record")

        if velocity > 0.7:
            effective_score += 0.10
            reasons.append(f"high velocity score ({velocity:.2f})")

        if identity_mismatch:
            effective_score += 0.15
            reasons.append("identity mismatch detected")

        effective_score = max(0.0, min(1.0, effective_score))

        # Classify
        risk_level = self._classify_risk(effective_score)
        flagged = risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}

        if not reasons:
            reasons.append(f"base fraud score {fraud_score:.2f}")

        return FraudVerdict(
            fraud_score=round(effective_score, 4),
            risk_level=risk_level,
            flagged=flagged,
            reasons=tuple(reasons),
        )

    def assert_not_fraudulent(
        self,
        fraud_score: float,
        threshold: float | None = None,
    ) -> None:
        """Raise if the fraud score exceeds the threshold.

        This is used as a hard gate — if this raises, the action MUST NOT proceed.
        """
        limit = threshold if threshold is not None else self._t["high"]
        if fraud_score >= limit:
            raise FraudBlockError(
                f"Fraud score {fraud_score:.4f} exceeds threshold {limit:.2f}"
            )

    def is_action_allowed(self, verdict: FraudVerdict, action: Action) -> bool:
        """Check if a specific action is allowed given the fraud verdict.

        §7.7: Amount and fraud risk gate autonomous action separately
        from model confidence.
        """
        if verdict.risk_level == RiskLevel.CRITICAL:
            # Only NO_ACTION, WAIT, BLOCK, HUMAN_ESCALATION allowed
            return action in {
                Action.NO_ACTION,
                Action.WAIT,
                Action.BLOCK,
                Action.HUMAN_ESCALATION,
            }

        if verdict.risk_level == RiskLevel.HIGH:
            # Block financial actions, allow communication
            return action not in _FRAUD_BLOCKED_ACTIONS

        # MEDIUM and LOW: all actions allowed
        return True

    def _classify_risk(self, score: float) -> RiskLevel:
        if score >= self._t["critical"]:
            return RiskLevel.CRITICAL
        if score >= self._t["high"]:
            return RiskLevel.HIGH
        if score >= self._t["medium"]:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


class FraudBlockError(RuntimeError):
    """Raised when an action is blocked due to fraud risk."""
