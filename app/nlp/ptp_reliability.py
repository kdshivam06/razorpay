"""PTP reliability scoring (§10.3)."""

from __future__ import annotations

import dataclasses
import enum


class ReliabilityBand(str, enum.Enum):
    """PTP reliability categories from §10.3."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclasses.dataclass(frozen=True)
class ReliabilityAssessment:
    """Reliability derived from promises vs fulfilled (§10.3)."""

    reliability_score: float
    band: ReliabilityBand
    promises: int
    fulfilled: int


class PtpReliabilityScorer:
    """Scores promise reliability; drives the hold period philosophy:

    HIGH (>75%)  → honor promised date
    MEDIUM (50-75%) → shorter hold
    LOW (<50%)   → do not accept PTP; human escalation
    """

    def score(self, promises: int, fulfilled: int) -> ReliabilityAssessment:
        if promises < 0 or fulfilled < 0:
            raise ValueError("promises and fulfilled cannot be negative")
        if fulfilled > promises:
            raise ValueError("fulfilled cannot exceed promises")
        reliability = 1.0 if promises == 0 else fulfilled / promises
        if reliability > 0.75:
            band = ReliabilityBand.HIGH
        elif reliability >= 0.50:
            band = ReliabilityBand.MEDIUM
        else:
            band = ReliabilityBand.LOW
        return ReliabilityAssessment(
            reliability_score=round(reliability, 3),
            band=band,
            promises=promises,
            fulfilled=fulfilled,
        )
