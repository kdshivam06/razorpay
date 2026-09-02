"""Module C — subscription dunning (§9.3).

States: created → authenticated → active → pending → halted → cancelled

CRITICAL: The mandate distinction is the #1 compliance trap.
  Customer-revoked mandate = PERMANENT STOP (harassment under RBI guidelines)
  Bank-revoked mandate = safe to send re-auth

Classifier MUST distinguish using error.description and error.source.
"""

from __future__ import annotations

import logging

from app.contracts import Action, CandidateAction
from app.core.recovery_case import RecoveryCase
from app.modules.base_module import BaseRecoveryModule

logger = logging.getLogger(__name__)


class SubscriptionDunningModule(BaseRecoveryModule):
    """Runs the subscription lifecycle: created → authenticated → active →
    pending → halted → cancelled (§9.3).

    The #1 compliance trap: customer-revoked mandate = PERMANENT STOP;
    bank-revoked = safe to send re-auth. Distinguish via error.source.
    """

    @property
    def module_name(self) -> str:
        return "subscription_dunning"

    def diagnose(self, case: RecoveryCase) -> str:
        """Classify the mandate failure type.

        §9.3: The critical distinction between customer-revoked and
        bank-revoked mandates.
        """
        rc = case.root_cause

        # Check communications for error source clues
        for comm in case.communications:
            error_source = comm.get("error_source", "")
            error_desc = comm.get("error_description", "")

            if error_source == "customer" or "customer" in error_desc.lower():
                return "mandate_revoked_customer"
            if "bank" in error_source.lower():
                return "mandate_revoked_bank"

        return rc or "mandate_failure"

    def generate_candidates(self, case: RecoveryCase) -> list[CandidateAction]:
        remaining = case.total_remaining()
        root_cause = self.diagnose(case)

        # ── PERMANENT STOP: customer-revoked mandate ──────────────
        if root_cause == "mandate_revoked_customer":
            logger.warning(
                "Case %s: CUSTOMER-REVOKED mandate — PERMANENT STOP. "
                "No recovery actions generated (RBI compliance).",
                case.case_id,
            )
            # Only NO_ACTION is valid — this is a compliance requirement
            return [
                CandidateAction(
                    action=Action.NO_ACTION,
                    expected_recovery_paise=0,
                    communication_cost_paise=0,
                    operational_cost_paise=0,
                    risk_penalty_paise=0,
                    cx_penalty_paise=0,
                    economic_score=0,
                )
            ]

        # ── Bank-revoked or transient failure ─────────────────────
        candidates: list[CandidateAction] = []

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

        # Re-auth request (bank-revoked only)
        if root_cause == "mandate_revoked_bank":
            candidates.append(
                CandidateAction(
                    action=Action.REQUEST_PAYMENT_METHOD_UPDATE,
                    expected_recovery_paise=int(remaining * 0.50),
                    communication_cost_paise=25,
                    operational_cost_paise=100,
                    risk_penalty_paise=0,
                    cx_penalty_paise=200,
                    economic_score=remaining * 0.50 - 325,
                )
            )

        # SMS with payment link
        candidates.append(
            CandidateAction(
                action=Action.SEND_SMS,
                expected_recovery_paise=int(remaining * 0.30),
                communication_cost_paise=25,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=150,
                economic_score=remaining * 0.30 - 175,
            )
        )

        # Payment link for manual payment
        candidates.append(
            CandidateAction(
                action=Action.SEND_PAYMENT_LINK,
                expected_recovery_paise=int(remaining * 0.45),
                communication_cost_paise=50,
                operational_cost_paise=100,
                risk_penalty_paise=0,
                cx_penalty_paise=200,
                economic_score=remaining * 0.45 - 350,
            )
        )

        # Retry same method (transient failures only)
        if root_cause not in {"mandate_revoked_bank", "mandate_revoked_customer"}:
            candidates.append(
                CandidateAction(
                    action=Action.RETRY_SAME_METHOD,
                    expected_recovery_paise=int(remaining * 0.40),
                    communication_cost_paise=0,
                    operational_cost_paise=200,
                    risk_penalty_paise=100,
                    cx_penalty_paise=0,
                    economic_score=remaining * 0.40 - 300,
                )
            )

        return candidates

    def build_payload(self, case: RecoveryCase, action: Action) -> dict:
        payload = super().build_payload(case, action)
        payload["template_key"] = "subscription_dunning"
        payload["variables"] = {
            "merchant_name": "Merchant",
            "amount": f"{case.total_remaining() / 100:.0f}",
            "cycle": "monthly",
            "link": f"https://rzp.io/sub/{case.case_id}",
        }
        return payload
