"""PTP date + amount extraction (§10.2)."""

from __future__ import annotations

import dataclasses
from datetime import date


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

    def extract(self, text: str, language: str = "hinglish") -> PtpExtraction:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §10.2")
