"""Recovery budget — budget-constrained optimization (§6.6)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action


@dataclasses.dataclass(frozen=True)
class BudgetAllocation:
    """Which cases get budget this cycle, and their action."""

    chosen_cases: list[str]
    actions: list[Action]
    expected_incremental_recovery_paise: int
    projected_cost_paise: int
    rejected_cases: list[str]


class RecoveryBudgetOptimizer:
    """Spends the merchant's daily communication budget where expected
    economic return is highest, ranking by
    Expected Incremental Recovery / Action Cost (§6.6)."""

    def allocate(
        self,
        cases: list[dict],
        daily_budget_paise: int,
        expected_incremental_recovery_paise: int,
        projected_cost_paise: int,
    ) -> BudgetAllocation:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.6")