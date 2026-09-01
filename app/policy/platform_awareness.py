"""Platform-aware recovery — what Razorpay already did (§7.6)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class PlatformActionHistory:
    """§7.6 — track actions Razorpay has already taken on a customer."""

    razorpay_notification_sent: bool
    razorpay_retry_pending: bool
    payment_link_exists: bool
    reminder_already_sent: bool
    subscription_retry_active: bool


class PlatformAwareness:
    """Suppresses agent actions that duplicate platform actions (§7.6).

    If Razorpay already failed-notified, the agent must NEVER duplicate an SMS.
    """

    def history_for(self, customer_id: str) -> PlatformActionHistory:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.6")

    def should_duplicate_sms(self, history: PlatformActionHistory) -> bool:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.6")