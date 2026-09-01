"""Dashboard data API — the recovery waterfall + scorecard (§14.1–14.6)."""

from __future__ import annotations

import dataclasses

from app.audit.decision_trace import DecisionTrace


@dataclasses.dataclass(frozen=True)
class Waterfall:
    """Collapsed dashboard outcome from §14.1."""

    revenue_at_risk_paise: int
    expected_natural_recovery_paise: int
    gross_recovery_opportunity_paise: int
    incremental_recovery_paise: int
    communication_cost_paise: int
    incremental_net_recovery_paise: int


class DashboardApi:
    """Serves the §14 waterfall/scorecard panels."""

    def waterfall(self) -> Waterfall:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §14.1"
        )

    def scorecard(self) -> dict[str, object]:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §14.2"
        )

    def decision_trace(self, case_id: str) -> DecisionTrace:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §13.3"
        )
