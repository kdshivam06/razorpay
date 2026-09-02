"""Module D — B2B Smart Collect & receivables (§9.4).

B2B Collection Prioritization — by expected incremental value,
NOT largest amount:
  ₹2L invoice, natural payment = 94% → low priority
  ₹70K invoice, natural payment = 25%, uplift = 45% → HIGH priority
"""

from __future__ import annotations

from app.contracts import Action, CandidateAction
from app.core.recovery_case import RecoveryCase
from app.modules.base_module import BaseRecoveryModule


class B2BReceivablesModule(BaseRecoveryModule):
    """B2B Smart Collect & receivables recovery (§9.4).

    Prioritizes by expected incremental value, not raw amount.
    """

    @property
    def module_name(self) -> str:
        return "b2b_receivables"

    def diagnose(self, case: RecoveryCase) -> str:
        return case.root_cause or "overdue_invoice"

    def generate_candidates(self, case: RecoveryCase) -> list[CandidateAction]:
        remaining = case.total_remaining()
        natural = case.natural_pay_probability
        candidates: list[CandidateAction] = []

        # NO_ACTION — B2B has high natural payment for reliable customers
        candidates.append(
            CandidateAction(
                action=Action.NO_ACTION,
                expected_recovery_paise=int(remaining * natural),
                communication_cost_paise=0,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=0,
                economic_score=remaining * natural * 0.5,
            )
        )

        # WAIT — if this customer typically pays within their delay window
        candidates.append(
            CandidateAction(
                action=Action.WAIT,
                expected_recovery_paise=int(remaining * min(natural + 0.15, 1.0)),
                communication_cost_paise=0,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=0,
                economic_score=remaining * min(natural + 0.10, 0.95) * 0.6,
            )
        )

        # Email reminder (B2B primary channel)
        candidates.append(
            CandidateAction(
                action=Action.SEND_EMAIL,
                expected_recovery_paise=int(remaining * 0.55),
                communication_cost_paise=10,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=100,
                economic_score=remaining * 0.55 - 110,
            )
        )

        # Payment link
        candidates.append(
            CandidateAction(
                action=Action.SEND_PAYMENT_LINK,
                expected_recovery_paise=int(remaining * 0.50),
                communication_cost_paise=50,
                operational_cost_paise=100,
                risk_penalty_paise=0,
                cx_penalty_paise=150,
                economic_score=remaining * 0.50 - 300,
            )
        )

        # Human escalation for high-value
        if remaining > 10000000:  # > ₹1L
            candidates.append(
                CandidateAction(
                    action=Action.HUMAN_ESCALATION,
                    expected_recovery_paise=int(remaining * 0.70),
                    communication_cost_paise=0,
                    operational_cost_paise=5000,
                    risk_penalty_paise=0,
                    cx_penalty_paise=0,
                    economic_score=remaining * 0.70 - 5000,
                )
            )

        # Partial payment offer
        if remaining > 5000000:  # > ₹50K
            candidates.append(
                CandidateAction(
                    action=Action.OFFER_PARTIAL_PAYMENT,
                    expected_recovery_paise=int(remaining * 0.40),
                    communication_cost_paise=50,
                    operational_cost_paise=500,
                    risk_penalty_paise=200,
                    cx_penalty_paise=100,
                    economic_score=remaining * 0.40 - 850,
                )
            )

        return candidates

    def build_payload(self, case: RecoveryCase, action: Action) -> dict:
        payload = super().build_payload(case, action)
        payload["template_key"] = "payment_failed_email"
        payload["variables"] = {
            "customer_name": case.customer_id,
            "merchant_name": "Merchant",
            "amount": f"{case.total_remaining() / 100:.0f}",
            "order_id": case.case_id,
            "failure_reason": "invoice overdue",
            "link": f"https://rzp.io/b2b/{case.case_id}",
            "expiry_date": "3 days from now",
        }
        return payload
