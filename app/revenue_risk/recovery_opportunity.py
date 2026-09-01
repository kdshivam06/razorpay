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
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.3")

    def best_incremental_action(
        self, amount_remaining: int, per_action_uplift: dict[Action, float]
    ) -> tuple[Action, float]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.3")


def opportunity_from_candidate(candidate: CandidateAction) -> float:
    raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.2")
