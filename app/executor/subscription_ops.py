"""Subscription operations — pause/resume/cancel (§9.3, §8)."""

from __future__ import annotations

import dataclasses

from app.contracts import ExecutionState, SubscriptionState


@dataclasses.dataclass(frozen=True)
class SubscriptionOpResult:
    """Result of a subscription write operation."""

    subscription_id: str
    new_state: SubscriptionState
    execution_state: ExecutionState
    detail: str = ""


class SubscriptionOps:
    """Pause/resume/cancel a subscription. The mandate_revoked_* direction is
    enforced upstream by the classifier (§9.3): customer-revoked mandates are a
    PERMANENT STOP and must never reach here."""

    def pause(self, subscription_id: str) -> SubscriptionOpResult:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §9.3, §8"
        )

    def resume(self, subscription_id: str) -> SubscriptionOpResult:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §9.3, §8"
        )

    def cancel(self, subscription_id: str) -> SubscriptionOpResult:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §9.3, §8"
        )
