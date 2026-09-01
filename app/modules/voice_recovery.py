"""Module F — voice recovery agent (mocked telephony, real compliance) (§9.6)."""

from __future__ import annotations

import dataclasses

from app.contracts import ConversationResult, Emotion, ExecutionState


@dataclasses.dataclass(frozen=True)
class VoiceCallResult:
    """Outcome of a (mocked) voice recovery call."""

    call_id: str
    state: ExecutionState
    conversation: ConversationResult | None
    summary: str


class VoiceRecoveryModule:
    """Demo flow: identity verification → Hinglish intent detection → PTP
    extraction → policy check → PTP creation (§9.6).

    Emotion-based escalation: on ANGRY/opt-out → stop voice, mark preference,
    human review."""

    def handle_call(
        self, call_id: str, transcript: str, emotion: Emotion | None = None
    ) -> VoiceCallResult:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.6")
