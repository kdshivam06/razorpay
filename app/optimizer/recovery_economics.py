"""Recovery economics engine — cost-benefit per action (§6.2)."""

from __future__ import annotations

from app.contracts import CandidateAction
from app.revenue_risk.uplift_model import UpliftEstimates


class RecoveryEconomics:
    """Computes Net Economic Value of each candidate action:

    Net = Expected Gross Recovery − Communication Cost − Operational Cost
          − Risk Penalty − CX Penalty        (§6.2)
    """

    def net_economic_value(
        self,
        expected_recovery: int,
        communication_cost: int,
        operational_cost: int,
        risk_penalty: int,
        cx_penalty: int,
    ) -> int:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.2")

    def score_candidates(
        self, estimates: UpliftEstimates, amount_remaining_paise: int
    ) -> list[CandidateAction]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.2")


class CommunicationCostTable:
    """Per-channel cost lookup used by the economics engine."""

    def cost_paise(self, channel: str) -> int:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.2")