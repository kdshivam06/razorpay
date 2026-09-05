"""Recovery budget — budget-constrained optimization (§6.6).

Merchant defines: daily_recovery_communication_budget: e.g. ₹10,000/day.
Cases are ranked by Expected Incremental Recovery / Action Cost.
The agent spends the budget where expected economic return is highest.
"""

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


@dataclasses.dataclass(frozen=True)
class CaseBudgetCandidate:
    """Internal ranking record for budget allocation."""

    case_id: str
    action: Action
    incremental_recovery_paise: int
    cost_paise: int
    roi: float  # incremental_recovery / cost (higher = better use of budget)


class RecoveryBudgetOptimizer:
    """Spends the merchant's daily communication budget where expected
    economic return is highest, ranking by
    Expected Incremental Recovery / Action Cost (§6.6).

    The allocator is greedy: pick the highest-ROI case, subtract cost from
    budget, repeat until budget exhausted or no positive-ROI cases remain.
    """

    def allocate(
        self,
        cases: list[dict],
        daily_budget_paise: int,
        expected_incremental_recovery_paise: int = 0,
        projected_cost_paise: int = 0,
    ) -> BudgetAllocation:
        """Allocate budget across cases.

        Each entry in ``cases`` must contain:
          - case_id: str
          - action: str (Action value) — the optimizer's recommended action
          - incremental_recovery_paise: int — expected incremental ₹
          - cost_paise: int — total cost of the action

        Cases with cost_paise <= 0 are treated as free (NO_ACTION/WAIT) and
        always included.
        """
        # Build ranked candidates
        candidates: list[CaseBudgetCandidate] = []
        free_cases: list[str] = []
        free_actions: list[Action] = []

        for entry in cases:
            case_id = entry["case_id"]
            try:
                action = Action(entry["action"])
            except (ValueError, KeyError):
                action = Action.NO_ACTION

            recovery = int(entry.get("incremental_recovery_paise", 0))
            cost = int(entry.get("cost_paise", 0))

            if cost <= 0:
                # Free actions (NO_ACTION, WAIT, etc.) always included
                free_cases.append(case_id)
                free_actions.append(action)
                continue

            roi = recovery / cost if cost > 0 else 0.0
            if roi <= 0:
                # Negative ROI — skip
                continue

            candidates.append(
                CaseBudgetCandidate(
                    case_id=case_id,
                    action=action,
                    incremental_recovery_paise=recovery,
                    cost_paise=cost,
                    roi=roi,
                )
            )

        # Sort by ROI descending (best use of budget first)
        candidates.sort(key=lambda c: c.roi, reverse=True)

        # Greedy allocation
        remaining_budget = daily_budget_paise
        chosen_cases: list[str] = list(free_cases)
        chosen_actions: list[Action] = list(free_actions)
        total_recovery = 0
        total_cost = 0
        rejected: list[str] = []

        for candidate in candidates:
            if candidate.cost_paise <= remaining_budget:
                chosen_cases.append(candidate.case_id)
                chosen_actions.append(candidate.action)
                total_recovery += candidate.incremental_recovery_paise
                total_cost += candidate.cost_paise
                remaining_budget -= candidate.cost_paise
            else:
                rejected.append(candidate.case_id)

        return BudgetAllocation(
            chosen_cases=chosen_cases,
            actions=chosen_actions,
            expected_incremental_recovery_paise=total_recovery,
            projected_cost_paise=total_cost,
            rejected_cases=rejected,
        )
