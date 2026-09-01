"""B2B customer payment profile (§9.4)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class B2BCustomerProfile:
    """A per-client payment behaviour profile (§9.4, §14.6)."""

    client_id: str
    avg_payment_delay_days: float
    ptp_reliability: float
    dispute_rate: float
    typical_payment_day_range: tuple[int, int]


class CustomerProfile:
    """Builds the §9.4 B2B profile so collectors don't aggressively chase a
    client that reliably pays late by design."""

    def profile_for(self, client_id: str) -> B2BCustomerProfile:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.4")
