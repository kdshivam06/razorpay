"""B2B collection prioritization — by expected incremental value (§9.4).

NOT by largest amount:
  ₹2L invoice, natural payment = 94% → low priority
  ₹70K invoice, natural payment = 25%, uplift = 45% → HIGH priority

Produces "Today's Top N Cases" ranked by expected incremental recovery
per unit of effort.
"""

from __future__ import annotations

import dataclasses
import logging

from app.core.recovery_case import RecoveryCase

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class CollectionItem:
    """One ranked B2B collection candidate."""

    case_id: str
    client_id: str
    amount_outstanding_paise: int
    natural_payment_probability: float
    estimated_uplift: float
    expected_incremental_recovery_paise: int
    effort_units: int
    recovery_per_effort: float
    rank: int = 0


class CollectionPrioritizer:
    """Ranks by expected incremental VALUE, not largest amount (§9.4).

    ₹2L invoice, natural payment 94% → LOW priority (they'll pay anyway)
    ₹70K invoice, natural 25%, uplift 45% → HIGH priority (our intervention matters)
    """

    def __init__(self, default_uplift: float = 0.20) -> None:
        self._default_uplift = default_uplift

    def rank(
        self,
        cases: list[RecoveryCase],
        limit: int = 20,
    ) -> list[CollectionItem]:
        """Rank B2B cases by expected incremental recovery per effort.

        Returns the top N cases a human collector should focus on today.
        """
        items: list[CollectionItem] = []

        for case in cases:
            remaining = case.total_remaining()
            if remaining <= 0:
                continue

            natural = case.natural_pay_probability
            # Estimate uplift — persuadables get higher uplift
            uplift = self._estimate_uplift(case)

            # Incremental recovery = amount * uplift probability
            incremental = int(remaining * uplift)

            # Effort units heuristic: higher amounts = more effort
            effort = max(1, remaining // 100000)  # 1 unit per ₹1000

            items.append(
                CollectionItem(
                    case_id=case.case_id,
                    client_id=case.customer_id,
                    amount_outstanding_paise=remaining,
                    natural_payment_probability=natural,
                    estimated_uplift=uplift,
                    expected_incremental_recovery_paise=incremental,
                    effort_units=effort,
                    recovery_per_effort=incremental / effort if effort > 0 else 0,
                )
            )

        # Sort by recovery_per_effort descending
        items.sort(key=lambda x: x.recovery_per_effort, reverse=True)

        # Assign ranks
        for i, item in enumerate(items[:limit]):
            item.rank = i + 1

        result = items[:limit]

        if result:
            logger.info(
                "B2B collection priority: top %d of %d cases. "
                "Best: %s (₹%s incremental, %.0f recovery/effort)",
                len(result),
                len(items),
                result[0].case_id,
                result[0].expected_incremental_recovery_paise / 100,
                result[0].recovery_per_effort,
            )

        return result

    def _estimate_uplift(self, case: RecoveryCase) -> float:
        """Estimate the uplift probability for a case."""
        natural = case.natural_pay_probability
        segment = str(case.uplift_segment)

        if segment == "SURE_THING" or natural > 0.85:
            return 0.02  # Almost no incremental value
        if segment == "SLEEPING_DOG":
            return 0.0  # Negative — don't touch
        if segment == "LOST_CAUSE" or natural < 0.10:
            return 0.05  # Very low
        if segment == "PERSUADABLE":
            return max(0.15, 0.60 - natural)  # Highest value
        # Default
        return self._default_uplift
