"""Out-of-order event ordering protection (§11.2)."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class EventState(str, enum.Enum):
    """Canonical payment states, ordered so only FORWARD transitions are allowed."""

    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"


@dataclass(frozen=True)
class StateVersion:
    """A state observation with the event's causal timestamp."""

    state: EventState
    ts: float


class OutOfOrderGuard:
    """Protects against events arriving out of order (§11.2, §11.1).

    Test: payment.captured arrives, then payment.authorized arrives later.
    Final state MUST remain CAPTURED, not AUTHORIZED."""

    def apply(self, current: EventState | None, incoming: StateVersion) -> EventState:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §11.2")
