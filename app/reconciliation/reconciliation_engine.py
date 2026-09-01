"""Reconciliation engine — periodic state reconciliation (§11.3)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Mismatch:
    """One detected state mismatch between two truth sources."""

    case_id: str
    field: str
    internal_value: str
    external_value: str
    auto_repairable: bool


@dataclasses.dataclass(frozen=True)
class ReconciliationReport:
    """Outcome of a reconciliation sweep."""

    mismatches: tuple[Mismatch, ...]
    repairable_count: int
    repaired_count: int


class ReconciliationEngine:
    """Periodically reconciles:

    internal ledger vs Razorpay API vs webhooks vs payment links vs subscription
    state (§11.1, §11.3). Auto-repairs ONLY safe forward transitions.
    """

    def reconcile(self, since_ts: float | None = None) -> ReconciliationReport:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §11.3")

    def repair_safe_transition(self, mismatch: Mismatch) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §11.3")
