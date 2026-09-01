"""Module B — checkout abandonment (§9.2)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class CheckoutAbandonmentCase:
    """A detected abandoned checkout."""

    session_id: str
    recoverable: bool
    reason: str


class CheckoutAbandonmentModule:
    """Detects checkout abandonment: no order.paid within timeout, partial
    checkout, or an on-dismiss callback (§9.2).

    Handles: cart changed after abandonment, multiple abandonments per session,
    anonymous users (no contact → UNRECOVERABLE), price-sensitive abandonment."""

    def detect(self, session_event: dict) -> CheckoutAbandonmentCase:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.2")
