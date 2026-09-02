"""Module A — payment degradation & infrastructure failures (§9.1).

Webhook signals: payment.downtime.started, payment.downtime.updated,
payment.downtime.resolved, payment.failed

Edge cases: single-bank downtime (not all), cascading downtimes,
flapping downtimes, payment fails before downtime webhook arrives.
"""

from __future__ import annotations

from app.contracts import Action, CandidateAction
from app.core.recovery_case import RecoveryCase
from app.modules.base_module import BaseRecoveryModule


class PaymentDegradationModule(BaseRecoveryModule):
    """Handles payment.downtime.* signals (§9.1)."""

    @property
    def module_name(self) -> str:
        return "payment_degradation"

    def diagnose(self, case: RecoveryCase) -> str:
        """Classify whether this is a bank timeout, gateway error, or
        instrument-specific failure."""
        rc = case.root_cause
        if rc in {"bank_timeout", "gateway_error"}:
            return rc
        # Default: check if it looks like infrastructure
        if any(o.remaining_amount > 0 for o in case.obligations):
            return "infrastructure_failure"
        return rc or "unknown_error"

    def generate_candidates(self, case: RecoveryCase) -> list[CandidateAction]:
        """Generate recovery candidates for payment degradation.

        1. Payment link with alternate method (primary)
        2. SMS notification about the issue
        3. Wait for downtime resolution (if severity is transient)
        4. NO_ACTION if natural payment probability is high
        """
        remaining = case.total_remaining()
        candidates: list[CandidateAction] = []

        # Always evaluate NO_ACTION
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

        # WAIT — if transient, wait for resolution
        candidates.append(
            CandidateAction(
                action=Action.WAIT,
                expected_recovery_paise=int(remaining * 0.6),
                communication_cost_paise=0,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=0,
                economic_score=remaining * 0.4,
            )
        )

        # Payment link with alternate method
        candidates.append(
            CandidateAction(
                action=Action.SEND_PAYMENT_LINK,
                expected_recovery_paise=int(remaining * 0.65),
                communication_cost_paise=50,
                operational_cost_paise=100,
                risk_penalty_paise=0,
                cx_penalty_paise=200,
                economic_score=remaining * 0.65 - 350,
            )
        )

        # SMS about the issue
        candidates.append(
            CandidateAction(
                action=Action.SEND_SMS,
                expected_recovery_paise=int(remaining * 0.35),
                communication_cost_paise=25,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=100,
                economic_score=remaining * 0.35 - 125,
            )
        )

        # Retry alternate method (if bank-specific failure)
        if case.root_cause in {"bank_timeout", "gateway_error"}:
            candidates.append(
                CandidateAction(
                    action=Action.RETRY_ALTERNATE_METHOD,
                    expected_recovery_paise=int(remaining * 0.55),
                    communication_cost_paise=0,
                    operational_cost_paise=200,
                    risk_penalty_paise=100,
                    cx_penalty_paise=0,
                    economic_score=remaining * 0.55 - 300,
                )
            )

        return candidates

    def build_payload(self, case: RecoveryCase, action: Action) -> dict:
        payload = super().build_payload(case, action)
        payload["failure_type"] = "infrastructure"
        payload["template_key"] = "payment_failed_sms"
        payload["variables"] = {
            "merchant_name": "Merchant",
            "amount": f"{case.total_remaining() / 100:.0f}",
            "order_id": case.case_id,
            "link": f"https://rzp.io/recovery/{case.case_id}",
        }
        return payload
