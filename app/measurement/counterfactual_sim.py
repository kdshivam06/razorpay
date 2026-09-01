"""Counterfactual strategy simulator (§12.3)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class StrategyComparison:
    """Comparison across strategies; clearly labelled as synthetic (§12.3)."""

    strategies: dict[str, dict]
    best_strategy: str


class CounterfactualSimulator:
    """Compares 'Fixed Rule Dunning vs AI Optimized vs Human Only' on
    synthetic simulations. Numbers are explicitly synthetic (§12.3)."""

    def run(
        self,
        strategies: dict[str, dict],
        synthetic_cases: list[dict],
        outcome_model: str = "ground_truth",
    ) -> StrategyComparison:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.3")
