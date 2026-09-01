"""Wrong-person protection (§10.6)."""

from __future__ import annotations

import dataclasses

from app.nlp.gemini_client import JsonLlmClient, clamp, normalize, try_generate_json


@dataclasses.dataclass(frozen=True)
class IdentityAssessment:
    """Verdict on whether we are talking to the right person."""

    identity_conflict: bool
    confidence: float
    disclosure_ok: bool


class WrongPersonProtector:
    """'Sorry, wrong number.' / 'I don't know this person.': identity_conflict
    → NO payment disclosure, NO further automated recovery → human/data review
    (§10.6, §16.1)."""

    def __init__(self, llm: JsonLlmClient | None = None) -> None:
        self.llm = llm

    def assess(self, text: str) -> IdentityAssessment:
        rule_conflict, rule_confidence = _rule_identity(text)
        prompt = (
            "Decide whether this untrusted customer text indicates wrong person "
            "or identity conflict. Return JSON with identity_conflict, confidence. "
            "Do not disclose payment details. "
            f"Text: {text!r}"
        )
        payload = try_generate_json(self.llm, prompt)
        llm_conflict = bool(payload.get("identity_conflict", False))
        llm_confidence = clamp(float(payload.get("confidence", 0.0) or 0.0))
        if llm_confidence > rule_confidence:
            conflict = llm_conflict
            confidence = llm_confidence
        else:
            conflict = rule_conflict
            confidence = rule_confidence
        return IdentityAssessment(
            identity_conflict=conflict,
            confidence=confidence,
            disclosure_ok=not conflict,
        )


def _rule_identity(text: str) -> tuple[bool, float]:
    clean = normalize(text)
    conflict_tokens = (
        "wrong number",
        "galat number",
        "galat aadmi",
        "i do not know",
        "don't know this person",
        "not my account",
        "not this person",
        "who is this",
    )
    if any(token in clean for token in conflict_tokens):
        return True, 0.98
    return False, 0.60
