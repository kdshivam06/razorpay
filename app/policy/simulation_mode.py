"""Policy simulation mode — SIMULATE before EXECUTE (§7.8)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action


@dataclasses.dataclass(frozen=True)
class SimulationReport:
    """Policy/optimizer feasibility report from §7.8. No external action yet."""

    cases_evaluated: int
    revenue_at_risk_paise: int
    expected_natural_recovery_paise: int
    expected_incremental_recovery_paise: int
    action_cost_paise: int
    compliance_violations: int
    human_reviews: int
    proposed_actions: dict[Action, int]


class SimulationMode:
    """Runs the full proposed pipeline in SIMULATE and ONLY THEN EXECUTE (§7.8).

    This is what makes the interactive judge demo safe — until the report is
    reviewed, no external action happens.
    """

    def simulate(
        self,
        cases: list[dict],
        strategy_name: str,
        *,
        execute_after: bool = False,
    ) -> SimulationReport:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §7.8"
        )
