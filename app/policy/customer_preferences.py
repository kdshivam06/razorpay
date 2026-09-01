"""Customer preference engine — legal ∩ merchant ∩ preference ∩ availability (§7.4)."""

from __future__ import annotations

import dataclasses
from datetime import datetime


@dataclasses.dataclass(frozen=True)
class ContactPreference:
    """One customer's preference over channels and windows."""

    customer_id: str
    preferred_channels: tuple[str, ...]
    blocked_channels: tuple[str, ...]
    do_not_call_before: str
    do_not_call_after: str
    allow_sunday: bool


class CustomerPreferenceEngine:
    """Intersects the four contact constraints:

    legal_window ∩ merchant_policy ∩ customer_preference ∩ channel_availability
    """

    def load_preferences(self, customer_id: str) -> ContactPreference:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.4")

    def can_contact(
        self, customer_id: str, channel: str, at: datetime
    ) -> bool:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.4")

    def allowed_window(
        self, customer_id: str, channel: str, at: datetime
    ) -> tuple[datetime, datetime]:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.4")