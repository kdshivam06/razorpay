"""Hinglish + emotion detection (§10.4)."""

from __future__ import annotations

import dataclasses

from app.contracts import Emotion
from app.nlp.gemini_client import JsonLlmClient, clamp, normalize, try_generate_json


@dataclasses.dataclass(frozen=True)
class EmotionResult:
    """Emotion + opt-out-like flag for a piece of customer text (§10.4)."""

    emotion: Emotion
    opt_out_like: bool
    confidence: float


class EmotionDetector:
    """Detects emotion and opt-out-like intent (e.g. 'kitni baar call
    karoge?! Stop this!') → STOP AUTOMATION → HUMAN REVIEW (§10.4, §9.6)."""

    def __init__(self, llm: JsonLlmClient | None = None) -> None:
        self.llm = llm

    def detect(self, text: str, language: str = "hinglish") -> EmotionResult:
        rule_emotion, rule_confidence = _rule_emotion(text)
        prompt = (
            "Detect customer emotion from untrusted payment recovery text. Return "
            "JSON with emotion, confidence, opt_out_like only. Valid emotions: "
            f"{[emotion.value for emotion in Emotion]}. "
            f"Language: {language}. Text: {text!r}"
        )
        payload = try_generate_json(self.llm, prompt)
        llm_emotion = _coerce_emotion(payload.get("emotion"))
        llm_confidence = clamp(float(payload.get("confidence", 0.0) or 0.0))
        opt_out_like = bool(payload.get("opt_out_like")) or _is_opt_out_like(text)
        if llm_emotion is not None and llm_confidence >= rule_confidence:
            return EmotionResult(llm_emotion, opt_out_like, llm_confidence)
        return EmotionResult(rule_emotion, opt_out_like, rule_confidence)


def _rule_emotion(text: str) -> tuple[Emotion, float]:
    clean = normalize(text)
    if any(
        token in clean
        for token in (
            "kitni baar",
            "angry",
            "gussa",
            "stop this",
            "harass",
            "baar baar",
        )
    ):
        return Emotion.ANGRY, 0.92
    if any(
        token in clean
        for token in ("frustrated", "pareshan", "issue", "problem", "galat")
    ):
        return Emotion.FRUSTRATED, 0.84
    if any(
        token in clean for token in ("worried", "anxious", "tension", "salary nahi")
    ):
        return Emotion.ANXIOUS, 0.82
    if any(token in clean for token in ("sorry", "sad", "unable")):
        return Emotion.SAD, 0.72
    if any(token in clean for token in ("thanks", "done", "paid", "will pay")):
        return Emotion.POSITIVE, 0.70
    return Emotion.NEUTRAL, 0.62


def _is_opt_out_like(text: str) -> bool:
    clean = normalize(text)
    return any(
        token in clean
        for token in ("stop", "mat karo", "unsubscribe", "do not contact")
    )


def _coerce_emotion(value: object) -> Emotion | None:
    try:
        return Emotion(str(value).upper())
    except ValueError:
        return None
