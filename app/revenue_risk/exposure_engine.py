"""Revenue-at-risk exposure engine (§4.2)."""

from __future__ import annotations

from app.contracts import RiskAssessment
from app.core.recovery_case import RecoveryCase


class RevenueAtRiskError(RuntimeError):
    """Raised when an obligation's revenue state cannot be resolved."""


class ExposureEngine:
    """Computes the per-case revenue-intelligence block from §4.2.

    Distinguishes the three terminology buckets the plan insists on:
      Revenue at Risk | Expected Natural Recovery | Incremental Recovery
    Amounts are integer paise. Fraud and dispute probability are 0..1.
    """

    def assess(self, case: RecoveryCase) -> RiskAssessment:
        if not case.obligations:
            raise RevenueAtRiskError(f"Case {case.case_id} has no obligations")

        amount_at_risk = sum(
            obligation.original_amount for obligation in case.obligations
        )
        amount_paid = sum(obligation.paid_amount for obligation in case.obligations)
        amount_refunded = sum(
            obligation.refunded_amount for obligation in case.obligations
        )
        amount_disputed = sum(
            obligation.disputed_amount for obligation in case.obligations
        )
        amount_remaining = case.total_remaining()
        natural_probability = _clamp(case.natural_pay_probability, 0.0, 1.0)
        expected_natural = self.expected_natural_recovery(
            amount_remaining, natural_probability
        )
        return RiskAssessment(
            amount_at_risk=amount_at_risk,
            amount_paid=amount_paid,
            amount_refunded=amount_refunded,
            amount_disputed=amount_disputed,
            amount_remaining=amount_remaining,
            natural_payment_probability=natural_probability,
            expected_natural_recovery=expected_natural,
            expected_days_to_payment=_expected_days(natural_probability),
            fraud_probability=_clamp(case.fraud_score, 0.0, 1.0),
            dispute_probability=_clamp(case.dispute_score, 0.0, 1.0),
            incremental_recovery_opportunity=self.incremental_recovery_opportunity(
                amount_remaining, expected_natural
            ),
        )

    def expected_natural_recovery(
        self,
        amount_remaining: int,
        natural_payment_probability: float,
    ) -> int:
        if amount_remaining < 0:
            raise RevenueAtRiskError("amount_remaining cannot be negative")
        probability = _clamp(natural_payment_probability, 0.0, 1.0)
        return round(amount_remaining * probability)

    def incremental_recovery_opportunity(
        self,
        amount_remaining: int,
        expected_natural_recovery: int,
    ) -> int:
        if amount_remaining < 0 or expected_natural_recovery < 0:
            raise RevenueAtRiskError("amounts cannot be negative")
        return max(0, amount_remaining - expected_natural_recovery)


def _expected_days(natural_payment_probability: float) -> float:
    return round(1.0 + (1.0 - natural_payment_probability) * 13.0, 2)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
