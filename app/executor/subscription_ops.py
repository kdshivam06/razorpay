"""Subscription operations — pause/resume/cancel (§9.3, §8).

The mandate_revoked_* direction is enforced upstream by the classifier (§9.3):
customer-revoked mandates are a PERMANENT STOP and must never reach here.
"""

from __future__ import annotations

import dataclasses
import logging

from app.contracts import ExecutionState, SubscriptionState

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class SubscriptionOpResult:
    """Result of a subscription write operation."""

    subscription_id: str
    new_state: SubscriptionState
    execution_state: ExecutionState
    detail: str = ""


class SubscriptionOps:
    """Pause/resume/cancel a subscription. Mocked for the hackathon."""

    def __init__(self) -> None:
        # Mock DB: sub_id -> state
        self._states: dict[str, SubscriptionState] = {}

    def get_state(self, subscription_id: str) -> SubscriptionState:
        return self._states.get(subscription_id, SubscriptionState.ACTIVE)

    def pause(self, subscription_id: str) -> SubscriptionOpResult:
        """Pause a subscription (e.g. while resolving a dispute or waiting for payment)."""
        logger.info("Pausing subscription %s", subscription_id)
        self._states[subscription_id] = SubscriptionState.HALTED
        return SubscriptionOpResult(
            subscription_id=subscription_id,
            new_state=SubscriptionState.HALTED,
            execution_state=ExecutionState.SUCCESS,
            detail="mocked pause",
        )

    def resume(self, subscription_id: str) -> SubscriptionOpResult:
        """Resume a halted subscription."""
        logger.info("Resuming subscription %s", subscription_id)
        self._states[subscription_id] = SubscriptionState.ACTIVE
        return SubscriptionOpResult(
            subscription_id=subscription_id,
            new_state=SubscriptionState.ACTIVE,
            execution_state=ExecutionState.SUCCESS,
            detail="mocked resume",
        )

    def cancel(self, subscription_id: str) -> SubscriptionOpResult:
        """Permanently cancel a subscription."""
        logger.warning("Cancelling subscription %s", subscription_id)
        self._states[subscription_id] = SubscriptionState.CANCELLED
        return SubscriptionOpResult(
            subscription_id=subscription_id,
            new_state=SubscriptionState.CANCELLED,
            execution_state=ExecutionState.SUCCESS,
            detail="mocked cancel",
        )
