"""Hinglish + emotion detection (§10.4)."""

from __future__ import annotations

import dataclasses

from app.contracts import Emotion


@dataclasses.dataclass(frozen=True)
class EmotionResult:
    """Emotion + opt-out-like flag for a piece of customer text (§10.4)."""

    emotion: Emotion
    opt_out_like: bool
    confidence: float


class EmotionDetector:
    """Detects emotion and opt-out-like intent (e.g. 'kitni baar call
    karoge?! Stop this!') → STOP AUTOMATION → HUMAN REVIEW (§10.4, §9.6)."""

    def detect(self, text: str, language: str = "hinglish") -> EmotionResult:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §10.4")
