"""Channel affinity model — per-customer channel preference (§5.6)."""

from __future__ import annotations

import dataclasses

from app.contracts import ContactChannel
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class ChannelAffinity:
    """Per-customer conversion by channel (§5.6)."""

    per_channel_conversion: dict[ContactChannel, float]
    preferred_channel: ContactChannel | None = None


class ChannelAffinityModel:
    """Maintains and predicts per-channel conversion rates, honouring
    language and customer-tailored features from §5.6."""

    def affinity_for(self, case: RecoveryCase) -> ChannelAffinity:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.6")

    def update_from_outcome(self, case_id: str, channel: ContactChannel, success: bool) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.6")


class ChannelAffinityStore:
    """Feature inputs for the affinity model (§5.6)."""

    def record(self, case_id: str, channel: ContactChannel, clicked: bool, opened: bool, latency_s: float) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.6")