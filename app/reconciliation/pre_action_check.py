"""Reconcile before every outbound action (§8.2)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class PreActionCheck:
    """Reconciliation verdict BEFORE an action executes."""

    allow: bool
    reason: str
    fresh_state: str | None


class PreActionReconciler:
    """Strongest demo feature (§8.2): before SMS/Email/Voice/Link/Retry,
    re-check the ledger — if the case is PAID 2 seconds ago, CANCEL the action."""

    def check(self, case: RecoveryCase, action: Action) -> PreActionCheck:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §8.2")