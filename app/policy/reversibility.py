"""Action reversibility scoring and RBAC gates (§6.4, §7.1)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action, ActionImpactLevel


@dataclasses.dataclass(frozen=True)
class ReversibilityAssessment:
    """Impact and autonomy classification of one action (§6.4)."""

    impact_level: ActionImpactLevel
    requires_human_approval: bool


class ReversibilityScorer:
    """Classifies actions by impact and autonomy (§6.4):

    Low (full auto):      WAIT, INTERNAL_TASK, DRAFT_MESSAGE, SCHEDULE_RETRY
    Medium (auto+policy): SMS, EMAIL, PAYMENT_LINK, VOICE
    High (human):         CHARGE, REFUND, CANCEL_SUBSCRIPTION, LEGAL_ESCALATION
    """

    def impact_level(self, action: Action) -> ActionImpactLevel:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §6.4"
        )

    def requires_human_approval(self, action: Action) -> bool:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §6.4"
        )
