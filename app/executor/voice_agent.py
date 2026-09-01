"""Voice recovery agent — Hinglish voice with real compliance (§9.6)."""

from __future__ import annotations

import dataclasses

from app.contracts import Emotion, ExecutionState


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
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §9.6")


class VoiceRecovery:
    """Runs the §9.6 demo flow: identity verification → Hinglish intent →
    PTP extraction → policy check → PTP creation. On ANGRY/opt-out intent it
    stops the voice flow, marks preference, and routes to human review."""

    def handle_call(self, call_id: str, transcript: str, emotion: Emotion | None = None) -> VoiceCallOutcome:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §9.6")