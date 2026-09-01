"""Intervention optimizer — economic action selection (§6.1, §6.2, §6.9)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action, CandidateAction, RecoveryStopReason
from app.core.recovery_case import RecoveryCase
from app.policy.policy_engine import PolicyGateResult
from app.revenue_risk.uplift_model import UpliftEstimates


@dataclasses.dataclass(frozen=True)
class OptimizationRecommendation:
    """The optimizer's single decision for a case."""

    selected_action: Action
    candidates: list[CandidateAction]
    economic_scores: dict[Action, float]
    stopped: bool = False
    stop_reason: RecoveryStopReason | None = None
    policy_gate: PolicyGateResult = PolicyGateResult.APPROVED
    rejected_reasons: dict[Action, str] = dataclasses.field(default_factory=dict)


class InterventionOptimizer:
    """Ranks all candidate actions by net economic value (§6.2) and picks the
    best policy-permitted one. Applies the three stopping rules (§6.9) BEFORE
    selecting any action. NO_ACTION is a first-class outcome, not a fallback.
    """

    def optimize(self, case: RecoveryCase, estimates: UpliftEstimates) -> OptimizationRecommendation:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §6.1, §6.2, §6.9")

    def build_candidates(
        self, case: RecoveryCase, estimates: UpliftEstimates
    ) -> list[CandidateAction]:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §6.2")

    def select_best(self, candidates: list[CandidateAction]) -> CandidateAction:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §6.2")

    def financial_stop_check(
        self,
        best: CandidateAction,
        amount_remaining: int,
        minimum_incremental_paise: int,
    ) -> RecoveryStopReason | None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §6.9")