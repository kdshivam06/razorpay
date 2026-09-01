"""14-intent classification taxonomy (§10.1)."""

from __future__ import annotations

import dataclasses

from app.contracts import ConversationalIntent
from app.nlp.gemini_client import JsonLlmClient, clamp, normalize, try_generate_json


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

    def __init__(self, llm: JsonLlmClient | None = None) -> None:
        self.llm = llm

    def classify(self, text: str, language: str = "hinglish") -> IntentResult:
        rule_intent, rule_confidence = _rule_intent(text)
        if rule_confidence >= 0.90:
            return IntentResult(
                intent=rule_intent,
                confidence=rule_confidence,
                opt_out_like=_is_opt_out_like(text)
                or rule_intent is ConversationalIntent.OPT_OUT,
            )

        prompt = (
            "Classify this untrusted recovery conversation text. Return JSON with "
            "intent, confidence, opt_out_like. Valid intents: "
            f"{[intent.value for intent in ConversationalIntent]}. "
            "Do not include execution instructions. "
            f"Language: {language}. Text: {text!r}"
        )
        payload = try_generate_json(self.llm, prompt)
        llm_intent = _coerce_intent(payload.get("intent"))
        llm_confidence = clamp(float(payload.get("confidence", 0.0) or 0.0))
        if llm_intent is not None and llm_confidence >= rule_confidence:
            return IntentResult(
                intent=llm_intent,
                confidence=llm_confidence,
                opt_out_like=bool(payload.get("opt_out_like"))
                or _is_opt_out_like(text),
            )

        return IntentResult(
            intent=rule_intent,
            confidence=rule_confidence,
            opt_out_like=_is_opt_out_like(text),
        )


def _rule_intent(text: str) -> tuple[ConversationalIntent, float]:
    clean = normalize(text)
    rules: tuple[tuple[ConversationalIntent, float, tuple[str, ...]], ...] = (
        (
            ConversationalIntent.WRONG_PERSON,
            0.98,
            (
                "wrong number",
                "galat aadmi",
                "i do not know",
                "don't know this person",
                "not this person",
            ),
        ),
        (
            ConversationalIntent.OPT_OUT,
            0.98,
            (
                "stop calling",
                "stop message",
                "mat karo",
                "unsubscribe",
                "do not contact",
                "don't contact",
                "opt out",
            ),
        ),
        (
            ConversationalIntent.SERVICE_NOT_RECEIVED,
            0.95,
            ("service not received", "item not delivered", "didn't get service"),
        ),
        (
            ConversationalIntent.PAYMENT_DISPUTE,
            0.94,
            ("dispute", "chargeback", "did not receive", "hold this payment"),
        ),
        (
            ConversationalIntent.AMOUNT_DISPUTE,
            0.94,
            ("amount wrong", "amount hi galat", "wrong amount", "incorrect amount"),
        ),
        (
            ConversationalIntent.ALREADY_PAID,
            0.94,
            ("already paid", "paid already", "payment ho gaya", "paid yesterday"),
        ),
        (
            ConversationalIntent.DUPLICATE_CHARGE,
            0.94,
            ("duplicate", "charged twice", "double charge", "do baar"),
        ),
        (
            ConversationalIntent.FRAUD_CLAIM,
            0.94,
            ("fraud", "unauthorized", "not authorised", "not authorized"),
        ),
        (
            ConversationalIntent.CANCEL_REQUEST,
            0.92,
            ("cancel", "close subscription", "stop auto debit"),
        ),
        (
            ConversationalIntent.REQUEST_INVOICE,
            0.90,
            ("invoice", "bill bhejo", "gst bill"),
        ),
        (
            ConversationalIntent.REQUEST_BANK_DETAILS,
            0.90,
            ("bank details", "account details", "ifsc", "neft"),
        ),
        (
            ConversationalIntent.UNABLE_TO_PAY,
            0.88,
            (
                "can't pay",
                "cannot pay",
                "unable to pay",
                "salary nahi",
                "paise nahi",
                "low balance",
            ),
        ),
        (
            ConversationalIntent.PROMISE_TO_PAY,
            0.86,
            (
                "pay tomorrow",
                "kar dunga",
                "promise",
                "salary ke baad",
                "month end",
                "friday",
                "next week",
            ),
        ),
        (
            ConversationalIntent.REQUEST_PAYMENT_LINK,
            0.82,
            ("payment link", "fresh link", "link bhejo", "retry link"),
        ),
    )
    for intent, confidence, tokens in rules:
        if any(token in clean for token in tokens):
            return intent, confidence
    return ConversationalIntent.REQUEST_PAYMENT_LINK, 0.45


def _is_opt_out_like(text: str) -> bool:
    clean = normalize(text)
    return any(
        token in clean
        for token in (
            "stop",
            "unsubscribe",
            "mat karo",
            "do not contact",
            "don't contact",
        )
    )


def _coerce_intent(value: object) -> ConversationalIntent | None:
    try:
        return ConversationalIntent(str(value).upper())
    except ValueError:
        return None
