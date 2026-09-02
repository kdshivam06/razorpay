"""Action reversibility scoring and RBAC gates (§6.4, §7.1, §7.7).

Classifies every action by impact level and determines whether human
approval is required.  High-impact actions (refund, cancel subscription)
always require human approval regardless of model confidence.

§7.7: Decision risk considers confidence, amount, fraud risk, dispute risk,
action reversibility, and customer sensitivity — NOT just model confidence.
"""

from __future__ import annotations

import dataclasses

from app.contracts import Action, ActionImpactLevel


@dataclasses.dataclass(frozen=True)
class ReversibilityAssessment:
    """Impact and autonomy classification of one action (§6.4)."""

    impact_level: ActionImpactLevel
    requires_human_approval: bool


# §6.4 classification — Low / Medium / High
_IMPACT_MAP: dict[Action, ActionImpactLevel] = {
    # Low (full auto): observe, wait, internal task, draft, schedule
    Action.NO_ACTION: ActionImpactLevel.LOW,
    Action.WAIT: ActionImpactLevel.LOW,
    Action.CREATE_PTP: ActionImpactLevel.LOW,
    # Medium (auto + policy): SMS, email, payment link, voice
    Action.SEND_SMS: ActionImpactLevel.MEDIUM,
    Action.SEND_EMAIL: ActionImpactLevel.MEDIUM,
    Action.SEND_WHATSAPP: ActionImpactLevel.MEDIUM,
    Action.SEND_PAYMENT_LINK: ActionImpactLevel.MEDIUM,
    Action.VOICE_CALL: ActionImpactLevel.MEDIUM,
    Action.RETRY_SAME_METHOD: ActionImpactLevel.MEDIUM,
    Action.RETRY_ALTERNATE_METHOD: ActionImpactLevel.MEDIUM,
    Action.REQUEST_PAYMENT_METHOD_UPDATE: ActionImpactLevel.MEDIUM,
    # High (human required): charge, refund, cancel, legal, block, partial payment
    Action.OFFER_PARTIAL_PAYMENT: ActionImpactLevel.HIGH,
    Action.HUMAN_ESCALATION: ActionImpactLevel.HIGH,
    Action.BLOCK: ActionImpactLevel.HIGH,
}

# Amount thresholds (paise) — above these, human approval is required
# regardless of impact level (§7.7: amount ₹5L → human approval)
_AMOUNT_HUMAN_THRESHOLD = 50000000  # ₹5,00,000


class ReversibilityScorer:
    """Classifies actions by impact and autonomy (§6.4):

    Low (full auto):      WAIT, NO_ACTION, CREATE_PTP
    Medium (auto+policy): SMS, EMAIL, PAYMENT_LINK, VOICE, RETRY
    High (human):         BLOCK, HUMAN_ESCALATION, OFFER_PARTIAL_PAYMENT
    """

    def __init__(
        self,
        amount_threshold_paise: int = _AMOUNT_HUMAN_THRESHOLD,
    ) -> None:
        self._amount_threshold = amount_threshold_paise

    def impact_level(self, action: Action) -> ActionImpactLevel:
        """Return the impact classification for an action."""
        return _IMPACT_MAP.get(
            action, ActionImpactLevel.HIGH
        )  # FAIL CLOSED: unknown → HIGH

    def requires_human_approval(
        self,
        action: Action,
        amount_paise: int = 0,
        fraud_risk_high: bool = False,
        customer_vip: bool = False,
    ) -> bool:
        """Determine if human approval is needed.

        §7.7 factors:
          - Action impact (HIGH → always human)
          - Amount (≥ ₹5L → human regardless of confidence)
          - Fraud risk (HIGH → human)
          - Customer sensitivity (VIP → human)
        """
        level = self.impact_level(action)

        # HIGH impact → always human
        if level == ActionImpactLevel.HIGH:
            return True

        # Amount check — large amounts always need human
        if amount_paise >= self._amount_threshold:
            return True

        # Fraud risk — high fraud → human for any financial action
        if fraud_risk_high and level == ActionImpactLevel.MEDIUM:
            return True

        # VIP customer — always human for MEDIUM+ actions
        return bool(customer_vip and level != ActionImpactLevel.LOW)

    def assess(
        self,
        action: Action,
        amount_paise: int = 0,
        fraud_risk_high: bool = False,
        customer_vip: bool = False,
    ) -> ReversibilityAssessment:
        """Full assessment combining impact level and human approval check."""
        return ReversibilityAssessment(
            impact_level=self.impact_level(action),
            requires_human_approval=self.requires_human_approval(
                action, amount_paise, fraud_risk_high, customer_vip
            ),
        )
