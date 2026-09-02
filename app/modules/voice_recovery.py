"""Module F — voice recovery agent (§9.6).

Demo flow:
  AI caller → identity verification → Hinglish intent detection
  → PTP extraction → policy check → PTP creation

Emotion-based escalation:
  Customer: "Kitni baar call karoge?! Stop this!"
  Detected: emotion = ANGRY, opt_out_intent = TRUE
  → Stop voice → mark preference → human review
"""

from __future__ import annotations

import logging

from app.contracts import Action, CandidateAction
from app.core.recovery_case import RecoveryCase
from app.modules.base_module import BaseRecoveryModule

logger = logging.getLogger(__name__)


class VoiceRecoveryModule(BaseRecoveryModule):
    """Mocked telephony, real compliance (§9.6).

    If customer is ANGRY or opts out, the module STOPS and routes
    to human review — never continues the call.
    """

    @property
    def module_name(self) -> str:
        return "voice_recovery"

    def diagnose(self, case: RecoveryCase) -> str:
        return case.root_cause or "ptp_broken"

    def generate_candidates(self, case: RecoveryCase) -> list[CandidateAction]:
        remaining = case.total_remaining()
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

        # Voice call — highest engagement, highest CX cost
        candidates.append(
            CandidateAction(
                action=Action.VOICE_CALL,
                expected_recovery_paise=int(remaining * 0.60),
                communication_cost_paise=500,
                operational_cost_paise=1000,
                risk_penalty_paise=200,
                cx_penalty_paise=500,
                economic_score=remaining * 0.60 - 2200,
            )
        )

        # SMS first (lower cost alternative)
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

        # WhatsApp (good for PTP extraction too)
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

        # Create PTP directly (if we already have a promise)
        for ptp in case.ptps:
            if ptp.get("status") == "PENDING":
                candidates.append(
                    CandidateAction(
                        action=Action.CREATE_PTP,
                        expected_recovery_paise=int(remaining * 0.70),
                        communication_cost_paise=0,
                        operational_cost_paise=0,
                        risk_penalty_paise=0,
                        cx_penalty_paise=0,
                        economic_score=remaining * 0.70,
                    )
                )
                break

        return candidates

    def build_payload(self, case: RecoveryCase, action: Action) -> dict:
        payload = super().build_payload(case, action)
        if action == Action.VOICE_CALL:
            payload["script"] = {
                "language": "hinglish",
                "identity_verification": True,
                "ptp_extraction": True,
                "emotion_detection": True,
                "stop_on_angry": True,
            }
        return payload
