"""Channel affinity model — per-customer channel preference (§5.6).

Maintains historical conversion rates per channel for each customer and exposes
a simple affinity API that the optimizer consults when choosing between
equal-value candidate actions.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict

from app.contracts import ContactChannel
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class ChannelAffinity:
    """Per-customer conversion by channel (§5.6)."""

    per_channel_conversion: dict[ContactChannel, float]
    preferred_channel: ContactChannel | None = None


# Default prior conversion rates when we have no customer history
_DEFAULT_PRIORS: dict[ContactChannel, float] = {
    ContactChannel.SMS: 0.12,
    ContactChannel.EMAIL: 0.05,
    ContactChannel.WHATSAPP: 0.18,
    ContactChannel.VOICE_CALL: 0.10,
    ContactChannel.PAYMENT_LINK: 0.22,
}


class ChannelAffinityStore:
    """In-memory feature store for channel interaction events (§5.6).

    In production this would be backed by Redis or PostgreSQL.  For the
    hackathon we keep an in-memory dict keyed by customer_id.
    """

    def __init__(self) -> None:
        # customer_id -> channel -> {attempts, successes, clicks, opens, total_latency}
        self._data: dict[str, dict[ContactChannel, dict[str, float]]] = defaultdict(
            lambda: defaultdict(
                lambda: {
                    "attempts": 0,
                    "successes": 0,
                    "clicks": 0,
                    "opens": 0,
                    "total_latency": 0.0,
                }
            )
        )

    def record(
        self,
        case_id: str,
        channel: ContactChannel,
        clicked: bool,
        opened: bool,
        latency_s: float,
    ) -> None:
        """Record a single interaction event for affinity learning."""
        # Use case_id as customer proxy (in production, look up customer_id)
        entry = self._data[case_id][channel]
        entry["attempts"] += 1
        if clicked:
            entry["clicks"] += 1
            entry["successes"] += 1
        if opened:
            entry["opens"] += 1
        entry["total_latency"] += latency_s

    def get_stats(self, customer_id: str) -> dict[ContactChannel, dict[str, float]]:
        return dict(self._data.get(customer_id, {}))


class ChannelAffinityModel:
    """Maintains and predicts per-channel conversion rates, honouring
    language and customer-tailored features from §5.6.

    Falls back to population-level priors when customer history is absent.
    """

    def __init__(
        self,
        store: ChannelAffinityStore | None = None,
        priors: dict[ContactChannel, float] | None = None,
    ) -> None:
        self._store = store or ChannelAffinityStore()
        self._priors = priors or dict(_DEFAULT_PRIORS)

    def affinity_for(self, case: RecoveryCase) -> ChannelAffinity:
        """Return the per-channel conversion map for a customer.

        Uses Bayesian-flavoured blending: posterior = (prior * α + observed * N) / (α + N)
        where α is the prior strength (set to 5 = "5 pseudo-observations").
        """
        stats = self._store.get_stats(case.customer_id)
        alpha = 5.0  # prior strength in pseudo-observations
        rates: dict[ContactChannel, float] = {}

        for channel in ContactChannel:
            prior = self._priors.get(channel, 0.10)
            history = stats.get(channel)
            if history and history["attempts"] > 0:
                observed_rate = history["successes"] / history["attempts"]
                n = history["attempts"]
                blended = (prior * alpha + observed_rate * n) / (alpha + n)
            else:
                blended = prior
            rates[channel] = round(blended, 4)

        # Preferred = highest conversion rate
        preferred = max(rates, key=rates.get) if rates else None  # type: ignore[arg-type]

        return ChannelAffinity(
            per_channel_conversion=rates,
            preferred_channel=preferred,
        )

    def update_from_outcome(
        self,
        case_id: str,
        channel: ContactChannel,
        success: bool,
    ) -> None:
        """Record a binary outcome for affinity learning."""
        self._store.record(
            case_id=case_id,
            channel=channel,
            clicked=success,
            opened=True,
            latency_s=0.0,
        )
