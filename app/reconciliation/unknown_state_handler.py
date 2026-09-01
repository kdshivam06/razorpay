"""UNKNOWN API-result reconciliation (§8.3)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action


@dataclasses.dataclass(frozen=True)
class UnknownResolution:
    """Resolution of an UNKNOWN outbound action result."""

    action: Action
    duplicate_effect_found: bool
    resolved: bool
    external_ref: str | None
    recommendation: str


class UnknownStateHandler:
    """Because 'Create Payment Link → timeout' means UNKNOWN, not FAILURE.

    On UNKNOWN: search/reconcile existing links, match by idempotency key, and
    only then decide. Never blind-retry (§8.3, §11.3)."""

    def resolve_unknown(self, case_id: str, action: Action) -> UnknownResolution:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §8.3")