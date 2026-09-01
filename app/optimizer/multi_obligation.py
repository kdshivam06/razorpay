"""Multi-obligation customer optimization (§6.7, §6.8)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class CustomerPortfolioDecision:
    """One communication + the highest-value recovery path per customer."""

    customer_id: str
    obligations_considered: list[str]
    selected_obligation_id: str
    selected_action: Action
    contact_budget_remaining: int
    rationale: str


class MultiObligationOptimizer:
    """Treats multiple obligations of one customer as a portfolio, NOT as
    independent chases, to reduce fatigue (§6.7)."""

    def optimize_customer(
        self,
        customer_cases: list[RecoveryCase],
        max_contacts_per_day: int,
    ) -> CustomerPortfolioDecision:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.7, §6.8")


class CustomerContactBudget:
    """Enforces the customer-level daily contact budget (§6.8)."""

    def remaining_today(self, customer_id: str) -> int:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.8")

    def consume(self, customer_id: str, amount: int = 1) -> bool:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.8")