"""Incremental recovery opportunity — monetary uplift per action (§5.3, §4.2)."""

from __future__ import annotations

from app.contracts import Action, CandidateAction


class RecoveryOpportunityEngine:
    """Converts propensity + uplift probabilities into expected euros/paise.

    Expected incremental recovery per action = uplift(action) * amount_remaining.
    Re-uses the plan's §6.2 economics vocabulary.
    """

    def opportunity_per_action(
        self,
        amount_remaining: int,
        per_action_uplift: dict[Action, float],
    ) -> dict[Action, float]:
        if amount_remaining < 0:
            raise ValueError("amount_remaining cannot be negative")
        return {
            action: max(0.0, float(uplift)) * amount_remaining
            for action, uplift in per_action_uplift.items()
        }

    def best_incremental_action(
        self, amount_remaining: int, per_action_uplift: dict[Action, float]
    ) -> tuple[Action, float]:
        opportunities = self.opportunity_per_action(amount_remaining, per_action_uplift)
        if not opportunities:
            return Action.NO_ACTION, 0.0
        best = max(opportunities, key=opportunities.get)
        if opportunities[best] <= 0:
            return Action.NO_ACTION, 0.0
        return best, opportunities[best]


def opportunity_from_candidate(candidate: CandidateAction) -> float:
    return float(candidate.economic_score)
