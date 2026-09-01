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
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §4.2")

    def expected_natural_recovery(
        self,
        amount_remaining: int,
        natural_payment_probability: float,
    ) -> int:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §4.2")

    def incremental_recovery_opportunity(
        self,
        amount_remaining: int,
        expected_natural_recovery: int,
    ) -> int:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §4.2")
