"""Wrong-person protection (§10.6)."""

from __future__ import annotations

import dataclasses


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

    def assess(self, text: str) -> IdentityAssessment:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §10.6")
