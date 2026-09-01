"""Module C — subscription dunning (§9.3)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action, SubscriptionState


@dataclasses.dataclass(frozen=True)
class DunningDecision:
    """What to do for a subscription in a given state."""

    subscription_id: str
    action: Action
    permanent_stop: bool
    reason: str


class SubscriptionDunningModule:
    """Runs the subscription lifecycle: created → authenticated → active →
    pending → halted → cancelled (§9.3).

    The #1 compliance trap: customer-revoked mandate = PERMANENT STOP (halt);
    bank-revoked = safe to send re-auth. Distinguish via error.source."""

    def dunning_step(self, subscription_id: str, state: SubscriptionState) -> DunningDecision:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.3")