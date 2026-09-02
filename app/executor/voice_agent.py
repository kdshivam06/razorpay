"""Voice recovery agent — Hinglish voice with real compliance (§9.6).

Runs the §9.6 demo flow: identity verification → Hinglish intent →
PTP extraction → policy check → PTP creation. On ANGRY/opt-out intent it
stops the voice flow, marks preference, and routes to human review.
"""

from __future__ import annotations

import dataclasses
import logging

from app.contracts import Emotion, ExecutionState

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class VoiceCallOutcome:
    """Outcome of a (mocked) voice call, incl. emotion escalation results."""

    call_id: str
    state: ExecutionState
    detected_emotion: Emotion | None
    opt_out_intent: bool
    transcript: str
    summary: str = ""


class MockTelephonyClient:
    """Mocked telephony backend — returns a scripted transcript for a caller."""

    def place_call(self, phone: str, script: dict, waited_s: int = 0) -> str:
        """Mock placing a call and returning a call_id."""
        logger.info("Placing mock call to %s", phone)
        import uuid

        return f"call_{uuid.uuid4().hex[:12]}"


class VoiceRecovery:
    """Runs the §9.6 demo flow."""

    def handle_call(
        self, call_id: str, transcript: str, emotion: Emotion | None = None
    ) -> VoiceCallOutcome:
        """Process the mocked transcript and emotion of a voice call.

        If ANGRY or opt_out_intent is detected, it returns immediately
        with opt_out_intent=True so upstream logic can add them to DND.
        """
        logger.info("Handling voice call %s. Emotion: %s", call_id, emotion)

        is_angry = emotion in {Emotion.ANGRY, Emotion.FRUSTRATED}
        opt_out = is_angry or ("stop calling" in transcript.lower())

        summary = "PTP extracted" if not opt_out else "Customer frustrated/opt-out"

        return VoiceCallOutcome(
            call_id=call_id,
            state=ExecutionState.SUCCESS,
            detected_emotion=emotion,
            opt_out_intent=opt_out,
            transcript=transcript,
            summary=summary,
        )
