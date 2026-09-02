"""B2B workload optimizer — top-N cases for collectors (§9.4).

Produces "Today's Top 20 Cases" for human collectors, ranked by expected
recovery / effort. Distributes cases across available collectors evenly.
"""

from __future__ import annotations

import dataclasses
import logging

from app.b2b.collection_priority import CollectionItem, CollectionPrioritizer
from app.core.recovery_case import RecoveryCase

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class CollectorWorkload:
    """The recommended collector workload for today."""

    collector_id: str
    cases: list[CollectionItem]
    total_expected_recovery_paise: int = 0
    case_count: int = 0

    def __post_init__(self) -> None:
        self.total_expected_recovery_paise = sum(
            c.expected_incremental_recovery_paise for c in self.cases
        )
        self.case_count = len(self.cases)


class WorkloadOptimizer:
    """Produces 'Today's Top N Cases' for human collectors, ranked by expected
    recovery / effort (§9.4).

    Distributes the ranked collection items across available collectors
    using round-robin assignment on the priority-ranked list.
    """

    def __init__(self, prioritizer: CollectionPrioritizer | None = None) -> None:
        self._prioritizer = prioritizer or CollectionPrioritizer()

    def plan(
        self,
        cases: list[RecoveryCase],
        collector_ids: list[str],
        limit_per_collector: int = 20,
    ) -> list[CollectorWorkload]:
        """Assign top cases to collectors using round-robin on priority rank."""
        if not collector_ids:
            logger.warning("No collectors available — cannot plan workload")
            return []

        total_limit = limit_per_collector * len(collector_ids)
        ranked = self._prioritizer.rank(cases, limit=total_limit)

        # Round-robin distribution
        workloads: dict[str, list[CollectionItem]] = {cid: [] for cid in collector_ids}

        for i, item in enumerate(ranked):
            collector = collector_ids[i % len(collector_ids)]
            if len(workloads[collector]) < limit_per_collector:
                workloads[collector].append(item)

        result = [
            CollectorWorkload(collector_id=cid, cases=items)
            for cid, items in workloads.items()
        ]

        for w in result:
            logger.info(
                "Collector %s: %d cases, expected recovery %s",
                w.collector_id,
                w.case_count,
                f"₹{w.total_expected_recovery_paise / 100:,.0f}",
            )

        return result
