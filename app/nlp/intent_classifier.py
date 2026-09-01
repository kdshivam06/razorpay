"""14-intent classification taxonomy (§10.1)."""

from __future__ import annotations

import dataclasses

from app.contracts import ConversationalIntent


@dataclasses.dataclass(frozen=True)
class IntentResult:
    """The labelled intent for a piece of customer text."""

    intent: ConversationalIntent
    confidence: float
    opt_out_like: bool


class IntentClassifier:
    """Classifies across the 14-intent taxonomy from §10.1 via LLM structured
    extraction + small rules (not a fine-tuned HingBERT — note MuRIL/HingBERT
    as the research direction)."""

    def classify(self, text: str, language: str = "hinglish") -> IntentResult:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §10.1")