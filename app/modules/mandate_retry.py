"""Module E — mandate retry sequencer (§9.5).

Uses dynamic timing model instead of fixed retry schedule.
Considers salary-day heuristic, bank maintenance windows,
customer payment patterns.
"""

from __future__ import annotations

from app.contracts import Action, CandidateAction
from app.core.recovery_case import RecoveryCase
from app.modules.base_module import BaseRecoveryModule


class MandateRetryModule(BaseRecoveryModule):
    """Dynamic mandate retry sequencer (§9.5).

    NOT a fixed "retry at T+1, T+3, T+7" schedule. Instead,
    uses customer payment patterns and bank availability.
    """

    @property
    def module_name(self) -> str:
        return "mandate_retry"

    def diagnose(self, case: RecoveryCase) -> str:
        rc = case.root_cause
        if rc == "insufficient_funds":
            return "insufficient_funds"
        if rc == "bank_timeout":
            return "bank_timeout"
        return rc or "mandate_failure"

    def generate_candidates(self, case: RecoveryCase) -> list[CandidateAction]:
        remaining = case.total_remaining()
        root_cause = self.diagnose(case)
        candidates: list[CandidateAction] = []

        # NO_ACTION
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

        # Retry same method — primary action for mandate failures
        # Score depends on root cause
        if root_cause == "insufficient_funds":
            # Wait for salary day, then retry
            retry_score = remaining * 0.55 - 200
        elif root_cause == "bank_timeout":
            # Bank issue — retry sooner
            retry_score = remaining * 0.65 - 100
        else:
            retry_score = remaining * 0.40 - 300

        candidates.append(
            CandidateAction(
                action=Action.RETRY_SAME_METHOD,
                expected_recovery_paise=int(remaining * 0.55),
                communication_cost_paise=0,
                operational_cost_paise=200,
                risk_penalty_paise=100,
                cx_penalty_paise=0,
                economic_score=retry_score,
            )
        )

        # SMS notification
        candidates.append(
            CandidateAction(
                action=Action.SEND_SMS,
                expected_recovery_paise=int(remaining * 0.25),
                communication_cost_paise=25,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=100,
                economic_score=remaining * 0.25 - 125,
            )
        )

        # Payment link (alternate method)
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

        # Payment method update (if expired card / stale mandate)
        if root_cause in {"expired_card", "mandate_failure"}:
            candidates.append(
                CandidateAction(
                    action=Action.REQUEST_PAYMENT_METHOD_UPDATE,
                    expected_recovery_paise=int(remaining * 0.50),
                    communication_cost_paise=25,
                    operational_cost_paise=100,
                    risk_penalty_paise=0,
                    cx_penalty_paise=150,
                    economic_score=remaining * 0.50 - 275,
                )
            )

        return candidates
