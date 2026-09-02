"""Module G — promise-to-pay NLP tracker (§9.7).

Enhanced with PTP amount extraction + reliability scoring.

Examples:
  "25 ko ₹20k de dunga"
  → { intent: PROMISE_TO_PAY, date: 2026-09-25, amount: 20000, confidence: 0.96 }

  "half abhi, baaki Friday"
  → split PTP with partial amounts

Reliability scoring:
  Promises = 5, Fulfilled = 4 → Reliability = 80%
  High (>75%) → Honor promised date
  Medium (50-75%) → Shorter hold period
  Low (<50%) → Don't accept PTP, human escalation
"""

from __future__ import annotations

import logging

from app.contracts import Action, CandidateAction
from app.core.recovery_case import RecoveryCase
from app.modules.base_module import BaseRecoveryModule

logger = logging.getLogger(__name__)


class PromiseToPayModule(BaseRecoveryModule):
    """PTP tracker with reliability scoring (§9.7, §10.2, §10.3)."""

    @property
    def module_name(self) -> str:
        return "promise_to_pay"

    def diagnose(self, case: RecoveryCase) -> str:
        # Check if there's an existing broken PTP
        for ptp in case.ptps:
            if ptp.get("status") == "BROKEN":
                return "ptp_broken"
        return case.root_cause or "insufficient_funds"

    def _ptp_reliability(self, case: RecoveryCase) -> float:
        """Calculate PTP reliability score (§10.3).

        Promises = N, Fulfilled = M → Reliability = M/N
        """
        ptps = case.ptps
        if not ptps:
            return 0.5  # Default for new customers

        total = len(ptps)
        fulfilled = sum(1 for p in ptps if p.get("status") == "FULFILLED")
        return fulfilled / total if total > 0 else 0.5

    def generate_candidates(self, case: RecoveryCase) -> list[CandidateAction]:
        remaining = case.total_remaining()
        reliability = self._ptp_reliability(case)
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

        # ── Reliability-based PTP decision (§10.3) ───────────────
        if reliability >= 0.75:
            # High reliability → honor the PTP, create and wait
            candidates.append(
                CandidateAction(
                    action=Action.CREATE_PTP,
                    expected_recovery_paise=int(remaining * reliability),
                    communication_cost_paise=0,
                    operational_cost_paise=0,
                    risk_penalty_paise=0,
                    cx_penalty_paise=0,
                    economic_score=remaining * reliability,
                )
            )
            # Also offer a gentle reminder
            candidates.append(
                CandidateAction(
                    action=Action.SEND_SMS,
                    expected_recovery_paise=int(remaining * 0.30),
                    communication_cost_paise=25,
                    operational_cost_paise=0,
                    risk_penalty_paise=0,
                    cx_penalty_paise=100,
                    economic_score=remaining * 0.30 - 125,
                )
            )
        elif reliability >= 0.50:
            # Medium reliability → shorter hold, with payment link
            candidates.append(
                CandidateAction(
                    action=Action.CREATE_PTP,
                    expected_recovery_paise=int(remaining * reliability * 0.8),
                    communication_cost_paise=0,
                    operational_cost_paise=0,
                    risk_penalty_paise=200,
                    cx_penalty_paise=0,
                    economic_score=remaining * reliability * 0.7,
                )
            )
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
        else:
            # Low reliability (<50%) → don't accept PTP, human escalation
            logger.info(
                "Case %s: PTP reliability %.0f%% is LOW — "
                "recommending human escalation instead of accepting PTP.",
                case.case_id,
                reliability * 100,
            )
            candidates.append(
                CandidateAction(
                    action=Action.HUMAN_ESCALATION,
                    expected_recovery_paise=int(remaining * 0.50),
                    communication_cost_paise=0,
                    operational_cost_paise=5000,
                    risk_penalty_paise=0,
                    cx_penalty_paise=0,
                    economic_score=remaining * 0.50 - 5000,
                )
            )
            candidates.append(
                CandidateAction(
                    action=Action.SEND_PAYMENT_LINK,
                    expected_recovery_paise=int(remaining * 0.35),
                    communication_cost_paise=50,
                    operational_cost_paise=100,
                    risk_penalty_paise=0,
                    cx_penalty_paise=200,
                    economic_score=remaining * 0.35 - 350,
                )
            )

        return candidates
