"""Module D (enhanced) — B2B smart collect & receivables (§9.4)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action


@dataclasses.dataclass(frozen=True)
class B2BCollectionDecision:
    """Prioritised / sequenced B2B collection action."""

    invoice_id: str
    action: Action
    expected_incremental_value: int
    reason: str


class B2BReceivablesModule:
    """Enhanced B2B smart collect — prioritises by expected incremental value,
    NOT largest amount (§9.4). Consumer of customer_profile + cashflow_forecast.
    Exposes 'Today's Top 20' workload for collectors."""

    def prioritize(self, invoices: list[dict]) -> list[B2BCollectionDecision]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.4")

    def top_workload(self, invoices: list[dict], limit: int = 20) -> list[str]:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §9.4")