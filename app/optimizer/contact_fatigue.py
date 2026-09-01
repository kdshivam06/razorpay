"""Contact fatigue engine — contact_fatigue_score (§6.3)."""

from __future__ import annotations

import dataclasses
import enum


class ContactFatigue(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    SEVERE = "SEVERE"


@dataclasses.dataclass(frozen=True)
class FatigueAssessment:
    """Fatigue score 0..1 plus a categorical level plus reason text."""

    score: float
    level: ContactFatigue
    reasons: tuple[str, ...]


class ContactFatigueEngine:
    """Scores customer fatigue from §6.3 features:

    contacts_last_1h, contacts_last_24h, contacts_last_7d,
    ignored_contacts, negative_replies, opt_outs, complaints,
    successful_contacts
    """

    def score(self, features: dict[str, float]) -> FatigueAssessment:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §6.3"
        )

    def should_pause_automation(self, assessment: FatigueAssessment) -> bool:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §6.3"
        )
