"""Contact policy engine — configurable contact windows (§7.2)."""

from __future__ import annotations

import dataclasses
import enum
from datetime import datetime, time


class Channel(str, enum.Enum):
    VOICE = "voice"
    SMS = "sms"
    EMAIL = "email"
    WHATSAPP = "whatsapp"


@dataclasses.dataclass(frozen=True)
class ChannelWindow:
    """A configurable per-channel allowed window (NOT hardcoded RBI hours)."""

    channel: Channel
    start: time
    end: time
    timezone_name: str = "Asia/Kolkata"
    allow_sunday: bool = True


class ContactPolicyEngine:
    """Enforces merchant-defined, configurable contact windows per channel,
    never a blanket 'RBI = 8 AM–7 PM' rule (§7.2)."""

    def load_from_yaml(self, path: str) -> None:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §7.2"
        )

    def is_contact_allowed(
        self, channel: Channel, at: datetime, customer_preference: tuple[str, ...]
    ) -> bool:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §7.2, §7.4"
        )


class ContactWindowEvaluator:
    """Evaluates contact legality at a given timestamp."""

    def allowed(self, channel: Channel, at: datetime) -> bool:
        raise NotImplementedError(
            "TODO: Policy track — see implementation_plan.md §7.2"
        )
