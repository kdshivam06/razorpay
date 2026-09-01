"""Module A — payment degradation & infrastructure failures (§9.1)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class DegradationDecision:
    """What the agent does in response to a payment-downtime signal."""

    action: str
    instrument: str | None
    reason: str
    severity: str


class PaymentDegradationModule:
    """Handles payment.downtime.* signals (§9.1):

    HIGH severity for an instrument → disable that payment option
    fail during downtime → payment link with alternate method
    downtime resolves → re-enable instrument, cancel pending links
    """

    def on_downtime_start(self, instrument: str, severity: str) -> DegradationDecision:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.1")

    def on_downtime_resolve(self, instrument: str) -> DegradationDecision:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.1")
