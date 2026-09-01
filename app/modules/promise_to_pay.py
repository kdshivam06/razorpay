"""Module G — promise-to-pay NLP tracker (§9.7, §10.2, §10.3)."""

from __future__ import annotations

import dataclasses

from app.nlp.ptp_extractor import PtpExtraction
from app.nlp.ptp_reliability import ReliabilityAssessment


@dataclasses.dataclass(frozen=True)
class PtpTrackerEntry:
    """A tracked PTP plus its extraction and reliability."""

    pledge_id: str
    customer_id: str
    extraction: PtpExtraction
    reliability: ReliabilityAssessment


class PromiseToPayTracker:
    """Tracks PTPs end-to-end: extraction (amount+date), reliability scoring,
    acceptance policy, and fulfilment history (§9.7, §10.2, §10.3)."""

    def register(
        self,
        customer_id: str,
        extraction: PtpExtraction,
        reliability: ReliabilityAssessment,
    ) -> PtpTrackerEntry:
        raise NotImplementedError(
            "TODO: ML track — see implementation_plan.md §9.7, §10.2, §10.3"
        )

    def honour_or_escalate(self, customer_id: str) -> str:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §10.3"
        )
