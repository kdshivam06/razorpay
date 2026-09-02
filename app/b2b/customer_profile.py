"""B2B customer payment profile (§9.4).

Per-client payment behaviour profile so collectors don't aggressively
chase a client that reliably pays late by design:

  ABC Pvt Ltd
    Avg payment delay: 3.2 days
    PTP reliability: 82%
    Dispute rate: 1%
    Typical payment day: 2nd–4th
  → Don't aggressively chase on day +1
"""

from __future__ import annotations

import dataclasses
import logging
import statistics

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class B2BCustomerProfile:
    """A per-client payment behaviour profile (§9.4, §14.6)."""

    client_id: str
    avg_payment_delay_days: float
    ptp_reliability: float
    dispute_rate: float
    typical_payment_day_range: tuple[int, int]
    total_invoices: int = 0
    on_time_rate: float = 0.0
    risk_tier: str = "MEDIUM"  # LOW / MEDIUM / HIGH


class CustomerProfiler:
    """Builds the §9.4 B2B profile from historical case data so collectors
    don't aggressively chase a client that reliably pays late by design.
    """

    def __init__(self) -> None:
        # client_id → list of historical payment records
        self._history: dict[str, list[dict]] = {}

    def ingest(self, client_id: str, record: dict) -> None:
        """Ingest a historical payment record for a client.

        Expected keys: delay_days, paid_on_time (bool), disputed (bool),
        payment_day (int 1-31), ptp_kept (bool or None).
        """
        self._history.setdefault(client_id, []).append(record)

    def profile_for(self, client_id: str) -> B2BCustomerProfile:
        """Build a payment behaviour profile from ingested history."""
        records = self._history.get(client_id, [])

        if not records:
            # No history → conservative defaults
            return B2BCustomerProfile(
                client_id=client_id,
                avg_payment_delay_days=5.0,
                ptp_reliability=0.5,
                dispute_rate=0.05,
                typical_payment_day_range=(1, 10),
                total_invoices=0,
                on_time_rate=0.5,
                risk_tier="MEDIUM",
            )

        delays = [r.get("delay_days", 0) for r in records]
        avg_delay = statistics.mean(delays) if delays else 5.0

        on_time = sum(1 for r in records if r.get("paid_on_time", False))
        on_time_rate = on_time / len(records)

        disputed = sum(1 for r in records if r.get("disputed", False))
        dispute_rate = disputed / len(records)

        ptp_records = [r for r in records if r.get("ptp_kept") is not None]
        ptp_kept = sum(1 for r in ptp_records if r.get("ptp_kept", False))
        ptp_reliability = (ptp_kept / len(ptp_records)) if ptp_records else 0.5

        pay_days = [r.get("payment_day", 15) for r in records]
        day_range = (min(pay_days), max(pay_days)) if pay_days else (1, 10)

        # Risk tier based on composite score
        if dispute_rate > 0.05 or ptp_reliability < 0.4:
            risk_tier = "HIGH"
        elif on_time_rate > 0.8 and dispute_rate < 0.02:
            risk_tier = "LOW"
        else:
            risk_tier = "MEDIUM"

        profile = B2BCustomerProfile(
            client_id=client_id,
            avg_payment_delay_days=round(avg_delay, 1),
            ptp_reliability=round(ptp_reliability, 2),
            dispute_rate=round(dispute_rate, 3),
            typical_payment_day_range=day_range,
            total_invoices=len(records),
            on_time_rate=round(on_time_rate, 2),
            risk_tier=risk_tier,
        )

        logger.info(
            "B2B profile for %s: delay=%.1f days, PTP=%.0f%%, "
            "dispute=%.1f%%, risk=%s (%d invoices)",
            client_id,
            profile.avg_payment_delay_days,
            profile.ptp_reliability * 100,
            profile.dispute_rate * 100,
            profile.risk_tier,
            profile.total_invoices,
        )
        return profile

    def should_chase(self, profile: B2BCustomerProfile, days_overdue: int) -> bool:
        """§9.4: Don't aggressively chase on day +1 if client typically pays
        on day +3.
        """
        return days_overdue > profile.avg_payment_delay_days
