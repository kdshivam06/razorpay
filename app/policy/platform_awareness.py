"""Platform-aware recovery — what Razorpay already did (§7.6).

The agent should NEVER duplicate a platform action.  If Razorpay already:
  - sent a failure notification → suppress duplicate SMS
  - created a payment link → reuse, don't create another
  - has a retry pending → wait for it
  - sent a reminder → suppress duplicate reminder

This module tracks what the platform has already done and suppresses
duplicate actions.
"""

from __future__ import annotations

import dataclasses

from app.contracts import Action
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class PlatformActionHistory:
    """§7.6 — track actions Razorpay has already taken on a customer."""

    razorpay_notification_sent: bool = False
    razorpay_retry_pending: bool = False
    payment_link_exists: bool = False
    reminder_already_sent: bool = False
    subscription_retry_active: bool = False


# Actions that duplicate specific platform actions
_PLATFORM_CONFLICT_MAP: dict[Action, str] = {
    Action.SEND_SMS: "razorpay_notification_sent",
    Action.SEND_PAYMENT_LINK: "payment_link_exists",
    Action.RETRY_SAME_METHOD: "razorpay_retry_pending",
    Action.RETRY_ALTERNATE_METHOD: "razorpay_retry_pending",
}


class PlatformAwareness:
    """Suppresses agent actions that duplicate platform actions (§7.6).

    If Razorpay already failed-notified, the agent must NEVER duplicate an SMS.
    """

    def __init__(self) -> None:
        # customer_id → PlatformActionHistory
        self._history: dict[str, PlatformActionHistory] = {}

    def history_for(self, customer_id: str) -> PlatformActionHistory:
        """Return the platform action history for a customer.

        Returns a default (all False) if no history is recorded.
        """
        return self._history.get(customer_id, PlatformActionHistory())

    def record_platform_action(
        self,
        customer_id: str,
        *,
        notification_sent: bool = False,
        retry_pending: bool = False,
        payment_link_exists: bool = False,
        reminder_sent: bool = False,
        subscription_retry: bool = False,
    ) -> None:
        """Record that the platform has taken an action."""
        existing = self.history_for(customer_id)
        self._history[customer_id] = PlatformActionHistory(
            razorpay_notification_sent=existing.razorpay_notification_sent
            or notification_sent,
            razorpay_retry_pending=existing.razorpay_retry_pending or retry_pending,
            payment_link_exists=existing.payment_link_exists or payment_link_exists,
            reminder_already_sent=existing.reminder_already_sent or reminder_sent,
            subscription_retry_active=existing.subscription_retry_active
            or subscription_retry,
        )

    def history_from_case(self, case: RecoveryCase) -> PlatformActionHistory:
        """Derive platform action history from the case's data."""
        has_link = bool(case.payment_links) and any(
            pl.get("state") in {"CREATED", "PARTIALLY_PAID"}
            for pl in case.payment_links
        )

        has_notification = any(
            c.get("source") == "razorpay" or c.get("platform") is True
            for c in case.communications
        )

        has_retry = any(
            c.get("action") in {"RETRY_SAME_METHOD", "RETRY_ALTERNATE_METHOD"}
            and c.get("source") == "razorpay"
            and c.get("outcome") in {None, "pending", "PENDING"}
            for c in case.communications
        )

        return PlatformActionHistory(
            razorpay_notification_sent=has_notification,
            razorpay_retry_pending=has_retry,
            payment_link_exists=has_link,
            reminder_already_sent=has_notification,
            subscription_retry_active=False,
        )

    def should_suppress(
        self, history: PlatformActionHistory, action: Action
    ) -> tuple[bool, str]:
        """Check if the action should be suppressed because the platform
        already took an equivalent action.

        Returns (suppress: bool, reason: str).
        """
        conflict_field = _PLATFORM_CONFLICT_MAP.get(action)
        if conflict_field is None:
            return (False, "")

        platform_already_did = getattr(history, conflict_field, False)
        if platform_already_did:
            return (
                True,
                f"platform already performed equivalent action ({conflict_field})",
            )

        return (False, "")

    def should_duplicate_sms(self, history: PlatformActionHistory) -> bool:
        """Should the agent send an SMS if Razorpay already sent a notification?

        Answer: NO. If platform already notified, suppress duplicate SMS.
        """
        return not history.razorpay_notification_sent
