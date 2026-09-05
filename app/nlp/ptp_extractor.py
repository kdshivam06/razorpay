"""PTP date + amount extraction (§10.2)."""

from __future__ import annotations

import dataclasses
import re
from calendar import monthrange
from datetime import date, timedelta

from app.contracts import ConversationalIntent
from app.core.clock import clock
from app.nlp.gemini_client import JsonLlmClient, clamp, normalize, try_generate_json


@dataclasses.dataclass(frozen=True)
class PtpExtraction:
    """Structured PTP extraction from customer text (§10.2)."""

    intent: str
    promised_date: date | None
    amount_paise: int | None
    currency: str
    confidence: float


class PtpExtractor:
    """Extracts PTP promise metadata, handling complex forms like:

    'half abhi, baaki Friday' | 'next salary ke baad' |
    'month-end tak clear' | '₹20k today, remaining next week'
    """

    def __init__(
        self, llm: JsonLlmClient | None = None, today: date | None = None
    ) -> None:
        self.llm = llm
        self.today = today or clock.today()

    def extract(self, text: str, language: str = "hinglish") -> PtpExtraction:
        rule = _rule_extract(text, self.today)
        prompt = (
            "Extract promise-to-pay metadata from untrusted Hinglish/English text. "
            "Return JSON only: intent, promised_date ISO yyyy-mm-dd or null, "
            "amount_paise integer or null, currency, confidence. Do not invent "
            "backend amounts. "
            f"Today is {self.today.isoformat()}. Language: {language}. Text: {text!r}"
        )
        payload = try_generate_json(self.llm, prompt)
        llm_result = _coerce_payload(payload)
        if llm_result is not None and llm_result.confidence >= rule.confidence:
            return llm_result
        return rule


def _rule_extract(text: str, today: date) -> PtpExtraction:
    clean = normalize(text)
    amount = _extract_amount_paise(clean)
    promised = _extract_date(clean, today)
    intent = (
        ConversationalIntent.PROMISE_TO_PAY.value
        if promised or amount or _looks_like_promise(clean)
        else ConversationalIntent.REQUEST_PAYMENT_LINK.value
    )
    confidence = 0.88 if intent == ConversationalIntent.PROMISE_TO_PAY.value else 0.52
    if amount is None:
        confidence -= 0.08
    if promised is None:
        confidence -= 0.08
    return PtpExtraction(intent, promised, amount, "INR", round(clamp(confidence), 3))


def _coerce_payload(payload: dict) -> PtpExtraction | None:
    if not payload:
        return None
    promised_date = payload.get("promised_date") or payload.get("date")
    parsed_date = None
    if promised_date:
        try:
            parsed_date = date.fromisoformat(str(promised_date))
        except ValueError:
            parsed_date = None
    amount = payload.get("amount_paise")
    if amount is None and payload.get("amount") is not None:
        amount = _rupees_to_paise(float(payload["amount"]))
    try:
        amount_paise = None if amount is None else int(amount)
    except (TypeError, ValueError):
        amount_paise = None
    intent = str(payload.get("intent") or ConversationalIntent.PROMISE_TO_PAY.value)
    confidence = clamp(float(payload.get("confidence", 0.0) or 0.0))
    return PtpExtraction(
        intent=intent,
        promised_date=parsed_date,
        amount_paise=amount_paise,
        currency=str(payload.get("currency") or "INR"),
        confidence=round(confidence, 3),
    )


def _extract_amount_paise(clean: str) -> int | None:
    amount_patterns = (
        r"(?:rs\.?|inr|₹)\s*([0-9]+(?:\.[0-9]+)?)\s*(k|l|lac|lakh)?",
        r"([0-9]+(?:\.[0-9]+)?)\s*(k|l|lac|lakh)\b",
    )
    for pattern in amount_patterns:
        match = re.search(pattern, clean)
        if match:
            value = float(match.group(1))
            suffix = (match.group(2) or "").lower()
            if suffix == "k":
                value *= 1_000
            elif suffix in {"l", "lac", "lakh"}:
                value *= 100_000
            return _rupees_to_paise(value)
    if "half" in clean or "aadha" in clean:
        return None
    return None


def _extract_date(clean: str, today: date) -> date | None:
    day_match = re.search(r"\b([1-2]?[0-9]|3[0-1])\s*(?:ko|tarikh|date)?\b", clean)
    if day_match and not re.search(r"(?:₹|rs\.?|inr)\s*" + day_match.group(1), clean):
        day = int(day_match.group(1))
        year = today.year
        month = today.month
        if day < today.day:
            month += 1
            if month == 13:
                month = 1
                year += 1
        day = min(day, monthrange(year, month)[1])
        return date(year, month, day)
    if "today" in clean or "aaj" in clean:
        return today
    if "tomorrow" in clean or "kal" in clean:
        return today + timedelta(days=1)
    if "next week" in clean or "agle hafte" in clean:
        return today + timedelta(days=7)
    if "salary" in clean:
        salary_day = 5
        month = today.month + (1 if today.day >= salary_day else 0)
        year = today.year + (1 if month == 13 else 0)
        month = 1 if month == 13 else month
        return date(year, month, salary_day)
    if "month end" in clean or "month-end" in clean or "mahine ke end" in clean:
        return date(today.year, today.month, monthrange(today.year, today.month)[1])
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, weekday in weekdays.items():
        if name in clean:
            delta = (weekday - today.weekday()) % 7
            return today + timedelta(days=delta or 7)
    return None


def _looks_like_promise(clean: str) -> bool:
    return any(
        token in clean
        for token in ("pay", "kar dunga", "clear", "de dunga", "promise", "baaki")
    )


def _rupees_to_paise(value: float) -> int:
    return round(value * 100)
