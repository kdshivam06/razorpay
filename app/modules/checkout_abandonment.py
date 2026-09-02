"""Module B — checkout abandonment (§9.2).

Detection: ondismiss callback, no order.paid within timeout, partial checkout.
Edge cases: cart changed after abandonment, multiple abandonments same session,
anonymous users (no contact info → UNRECOVERABLE), price-sensitive abandonment.
"""

from __future__ import annotations

from app.contracts import Action, CandidateAction
from app.core.recovery_case import RecoveryCase
from app.modules.base_module import BaseRecoveryModule


class CheckoutAbandonmentModule(BaseRecoveryModule):
    """Handles checkout abandonment recovery (§9.2)."""

    @property
    def module_name(self) -> str:
        return "checkout_abandonment"

    def diagnose(self, case: RecoveryCase) -> str:
        return case.root_cause or "checkout_abandoned"

    def generate_candidates(self, case: RecoveryCase) -> list[CandidateAction]:
        remaining = case.total_remaining()
        candidates: list[CandidateAction] = []

        # NO_ACTION — high natural return rate for some segments
        candidates.append(
            CandidateAction(
                action=Action.NO_ACTION,
                expected_recovery_paise=int(remaining * case.natural_pay_probability),
                communication_cost_paise=0,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=0,
                economic_score=remaining * case.natural_pay_probability * 0.5,
            )
        )

        # Payment link (primary recovery action for abandonment)
        candidates.append(
            CandidateAction(
                action=Action.SEND_PAYMENT_LINK,
                expected_recovery_paise=int(remaining * 0.45),
                communication_cost_paise=50,
                operational_cost_paise=100,
                risk_penalty_paise=0,
                cx_penalty_paise=300,
                economic_score=remaining * 0.45 - 450,
            )
        )

        # Email nudge (lower CX penalty than SMS for abandonment)
        candidates.append(
            CandidateAction(
                action=Action.SEND_EMAIL,
                expected_recovery_paise=int(remaining * 0.30),
                communication_cost_paise=10,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=100,
                economic_score=remaining * 0.30 - 110,
            )
        )

        # WhatsApp (higher engagement than email)
        candidates.append(
            CandidateAction(
                action=Action.SEND_WHATSAPP,
                expected_recovery_paise=int(remaining * 0.40),
                communication_cost_paise=75,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=200,
                economic_score=remaining * 0.40 - 275,
            )
        )

        return candidates

    def build_payload(self, case: RecoveryCase, action: Action) -> dict:
        payload = super().build_payload(case, action)
        payload["template_key"] = "payment_failed_sms"
        payload["variables"] = {
            "merchant_name": "Merchant",
            "amount": f"{case.total_remaining() / 100:.0f}",
            "order_id": case.case_id,
            "link": f"https://rzp.io/checkout/{case.case_id}",
        }
        return payload
