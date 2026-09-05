"""Contact fatigue engine — contact_fatigue_score (§6.3).

Beyond simple cooldowns (handled by policy/cooldown_manager.py), this module
computes a holistic fatigue score from recent contact history, response rates,
and negative signals.  When fatigue is HIGH or SEVERE the optimizer treats
outbound automation as blocked.
"""

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


# ---------------------------------------------------------------------------
# Thresholds (configurable, sane defaults from §6.3)
# ---------------------------------------------------------------------------

_THRESHOLDS = {
    "contacts_1h_limit": 2,
    "contacts_24h_limit": 4,
    "contacts_7d_limit": 10,
    "ignored_ratio_high": 0.7,  # ≥70 % ignored → HIGH
    "negative_reply_limit": 1,
    "opt_out_immediate": True,  # any opt-out → SEVERE
    "complaint_immediate": True,  # any complaint → SEVERE
}


class ContactFatigueEngine:
    """Scores customer fatigue from §6.3 features:

    contacts_last_1h, contacts_last_24h, contacts_last_7d,
    ignored_contacts, negative_replies, opt_outs, complaints,
    successful_contacts
    """

    def __init__(
        self,
        thresholds: dict[str, float | int | bool] | None = None,
    ) -> None:
        self._t = dict(_THRESHOLDS)
        if thresholds:
            self._t.update(thresholds)

    def score(self, features: dict[str, float]) -> FatigueAssessment:
        """Compute fatigue assessment from raw contact features.

        features keys (all numeric, default 0 if absent):
            contacts_last_1h, contacts_last_24h, contacts_last_7d,
            ignored_contacts, negative_replies, opt_outs, complaints,
            successful_contacts, total_contacts
        """
        reasons: list[str] = []
        raw_score = 0.0

        contacts_1h = features.get("contacts_last_1h", 0)
        contacts_24h = features.get("contacts_last_24h", 0)
        contacts_7d = features.get("contacts_last_7d", 0)
        ignored = features.get("ignored_contacts", 0)
        negative = features.get("negative_replies", 0)
        opt_outs = features.get("opt_outs", 0)
        complaints = features.get("complaints", 0)
        successful = features.get("successful_contacts", 0)
        total = features.get("total_contacts", contacts_7d) or 1.0

        # --- immediate escalation flags ---
        if opt_outs > 0 and self._t.get("opt_out_immediate"):
            reasons.append(f"customer opted out ({int(opt_outs)} opt-out(s))")
            return FatigueAssessment(
                score=1.0, level=ContactFatigue.SEVERE, reasons=tuple(reasons)
            )

        if complaints > 0 and self._t.get("complaint_immediate"):
            reasons.append(f"customer complaint recorded ({int(complaints)})")
            return FatigueAssessment(
                score=1.0, level=ContactFatigue.SEVERE, reasons=tuple(reasons)
            )

        # --- frequency pressure ---
        if contacts_1h >= self._t["contacts_1h_limit"]:
            raw_score += 0.30
            reasons.append(
                f"{int(contacts_1h)} contacts in last 1h (limit {self._t['contacts_1h_limit']})"
            )

        if contacts_24h >= self._t["contacts_24h_limit"]:
            raw_score += 0.25
            reasons.append(
                f"{int(contacts_24h)} contacts in last 24h (limit {self._t['contacts_24h_limit']})"
            )

        if contacts_7d >= self._t["contacts_7d_limit"]:
            raw_score += 0.15
            reasons.append(
                f"{int(contacts_7d)} contacts in last 7d (limit {self._t['contacts_7d_limit']})"
            )

        # --- response quality ---
        ignored_ratio = ignored / total if total > 0 else 0.0
        if ignored_ratio >= self._t["ignored_ratio_high"]:
            raw_score += 0.25
            reasons.append(f"{ignored_ratio:.0%} of contacts ignored")

        if negative >= self._t["negative_reply_limit"]:
            raw_score += 0.20
            reasons.append(f"{int(negative)} negative replies")

        # --- positive signal reduces fatigue ---
        if successful > 0 and total > 0:
            success_ratio = successful / total
            raw_score -= success_ratio * 0.15

        final_score = max(0.0, min(1.0, raw_score))
        level = _level_from_score(final_score)

        if not reasons:
            reasons.append("low contact pressure")

        return FatigueAssessment(
            score=round(final_score, 4), level=level, reasons=tuple(reasons)
        )

    def should_pause_automation(self, assessment: FatigueAssessment) -> bool:
        """Return True when automated outreach should STOP (§6.3).

        HIGH or SEVERE fatigue → automation paused.
        """
        return assessment.level in {ContactFatigue.HIGH, ContactFatigue.SEVERE}


def _level_from_score(score: float) -> ContactFatigue:
    if score >= 0.80:
        return ContactFatigue.SEVERE
    if score >= 0.55:
        return ContactFatigue.HIGH
    if score >= 0.30:
        return ContactFatigue.MEDIUM
    return ContactFatigue.LOW
