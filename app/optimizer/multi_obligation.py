"""Multi-obligation customer optimization (§6.7, §6.8).

When a customer owes on multiple obligations (Invoice A ₹20K, Invoice B ₹50K,
Subscription ₹10K), the system does NOT chase each independently.  A customer-
level optimizer chooses ONE communication + the highest-value recovery path to
reduce fatigue.

This also enforces the customer-level daily contact budget (§6.8).
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict

from app.contracts import Action
from app.core.obligation import ObligationStatus
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


class CustomerContactBudget:
    """Enforces the customer-level daily contact budget (§6.8).

    In production this would be backed by Redis with daily TTL keys.
    For the hackathon we use an in-memory dict.
    """

    def __init__(self, default_daily_limit: int = 2) -> None:
        self._default_limit = default_daily_limit
        # customer_id -> contacts consumed today
        self._consumed: dict[str, int] = defaultdict(int)

    def remaining_today(self, customer_id: str) -> int:
        """How many more contacts we can make today for this customer."""
        return max(0, self._default_limit - self._consumed[customer_id])

    def consume(self, customer_id: str, amount: int = 1) -> bool:
        """Consume contact budget.  Returns True if budget was available."""
        if self.remaining_today(customer_id) < amount:
            return False
        self._consumed[customer_id] += amount
        return True

    def reset(self, customer_id: str | None = None) -> None:
        """Reset budget (e.g. at midnight).  None = reset all."""
        if customer_id:
            self._consumed[customer_id] = 0
        else:
            self._consumed.clear()


class MultiObligationOptimizer:
    """Treats multiple obligations of one customer as a portfolio, NOT as
    independent chases, to reduce fatigue (§6.7).

    Strategy:
      1. Filter to non-terminal obligations.
      2. Score each obligation by remaining_amount × estimated recovery potential.
      3. Pick the obligation with the highest expected incremental value.
      4. Use at most one contact slot from the customer's daily budget.
    """

    def __init__(
        self,
        contact_budget: CustomerContactBudget | None = None,
    ) -> None:
        self._budget = contact_budget or CustomerContactBudget()

    def optimize_customer(
        self,
        customer_cases: list[RecoveryCase],
        max_contacts_per_day: int = 2,
    ) -> CustomerPortfolioDecision:
        """Select the single best obligation + action across all cases.

        ``customer_cases`` are all RecoveryCases belonging to the same customer.
        Each case may have multiple obligations.
        """
        if not customer_cases:
            raise ValueError("No cases provided for customer optimization")

        customer_id = customer_cases[0].customer_id

        # Flatten to (case, obligation) pairs, filter terminals
        candidates: list[tuple[RecoveryCase, str, int]] = []
        all_obligation_ids: list[str] = []

        for case in customer_cases:
            for obligation in case.obligations:
                all_obligation_ids.append(obligation.obligation_id)
                if obligation.is_terminal:
                    continue
                if obligation.status == ObligationStatus.DISPUTED:
                    continue
                # Score = remaining amount (higher = more valuable to recover)
                # In production this would use the uplift model's incremental value
                score = obligation.remaining_amount
                candidates.append((case, obligation.obligation_id, score))

        if not candidates:
            # All obligations are terminal — nothing to do
            return CustomerPortfolioDecision(
                customer_id=customer_id,
                obligations_considered=all_obligation_ids,
                selected_obligation_id="",
                selected_action=Action.NO_ACTION,
                contact_budget_remaining=self._budget.remaining_today(customer_id),
                rationale="all obligations terminal or disputed — NO_ACTION",
            )

        # Sort by score descending — pick highest-value obligation
        candidates.sort(key=lambda t: t[2], reverse=True)
        best_case, best_obligation_id, best_score = candidates[0]

        # Check contact budget
        remaining = self._budget.remaining_today(customer_id)
        if remaining <= 0:
            return CustomerPortfolioDecision(
                customer_id=customer_id,
                obligations_considered=all_obligation_ids,
                selected_obligation_id=best_obligation_id,
                selected_action=Action.NO_ACTION,
                contact_budget_remaining=0,
                rationale=f"contact budget exhausted (limit {max_contacts_per_day}/day) — NO_ACTION",
            )

        # Select an action — delegate to the case's best_action if set, else SEND_PAYMENT_LINK
        selected_action = Action.NO_ACTION
        if best_case.best_action:
            try:
                selected_action = Action(best_case.best_action)
            except ValueError:
                selected_action = Action.SEND_PAYMENT_LINK
        else:
            selected_action = Action.SEND_PAYMENT_LINK

        # Consume one contact slot
        self._budget.consume(customer_id, 1)

        rationale_parts = [
            f"selected obligation {best_obligation_id} (₹{best_score / 100:.0f} remaining)",
            f"from {len(candidates)} active obligations across {len(customer_cases)} cases",
        ]
        if len(candidates) > 1:
            rationale_parts.append(
                f"deferred {len(candidates) - 1} lower-priority obligation(s) to reduce fatigue"
            )

        return CustomerPortfolioDecision(
            customer_id=customer_id,
            obligations_considered=all_obligation_ids,
            selected_obligation_id=best_obligation_id,
            selected_action=selected_action,
            contact_budget_remaining=self._budget.remaining_today(customer_id),
            rationale=" — ".join(rationale_parts),
        )
