"""B2B workload optimizer — top-N cases for collectors (§9.4)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Workload:
    """The recommended collector workload."""

    collector_id: str
    case_ids: list[str]
    expected_recovery_paise: int


class WorkloadOptimizer:
    """Produces 'Today's Top 20 Cases' for human collectors, ranked by expected
    recovery / effort (§9.4)."""

    def plan(self, cases: list[dict], collector_ids: list[str], limit: int = 20) -> list[Workload]:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §9.4")