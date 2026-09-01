"""B2B collection prioritization — by expected incremental value (§9.4)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class CollectionItem:
    """One ranked B2B collection candidate."""

    invoice_id: str
    expected_incremental_recovery_paise: int
    effort_units: int


class CollectionPrioritizer:
    """Ranks by expected incremental VALUE, not largest amount (§9.4):

      ₹2L invoice, natural payment 94% → LOW priority
      ₹70K invoice, natural 25%, uplift 45% → HIGH priority.
    """

    def rank(self, items: list[CollectionItem]) -> list[CollectionItem]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.4")