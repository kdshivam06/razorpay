"""Out-of-order event ordering protection (§11.2).

Razorpay confirms: ordering of webhook delivery is NOT guaranteed.
Test: `payment.captured` arrives first, then `payment.authorized` arrives later.
Final state MUST remain CAPTURED, not AUTHORIZED.

Implementation: event states have a precedence ordering. Only allow
forward transitions. A late `AUTHORIZED` after `CAPTURED` is a no-op.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class EventState(str, enum.Enum):
    """Canonical payment states, ordered so only FORWARD transitions are allowed.

    The integer precedence is the ordering:
      CREATED(0) < AUTHORIZED(1) < CAPTURED(2) < SETTLED(3)
      REFUNDED(4) and DISPUTED(5) are terminal / special.

    A late AUTHORIZED arriving after CAPTURED must NOT regress state.
    """

    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    SETTLED = "SETTLED"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"
    FAILED = "FAILED"


# Strict precedence ordering — higher number = further along the lifecycle
_PRECEDENCE: dict[EventState, int] = {
    EventState.CREATED: 0,
    EventState.AUTHORIZED: 1,
    EventState.CAPTURED: 2,
    EventState.SETTLED: 3,
    EventState.REFUNDED: 4,
    EventState.DISPUTED: 5,
    EventState.FAILED: 99,  # FAILED is terminal but doesn't block higher states
}

# Allowed forward transitions (from → set of valid targets)
_VALID_TRANSITIONS: dict[EventState, frozenset[EventState]] = {
    EventState.CREATED: frozenset(
        {EventState.AUTHORIZED, EventState.CAPTURED, EventState.FAILED}
    ),
    EventState.AUTHORIZED: frozenset(
        {EventState.CAPTURED, EventState.REFUNDED, EventState.FAILED}
    ),
    EventState.CAPTURED: frozenset(
        {EventState.SETTLED, EventState.REFUNDED, EventState.DISPUTED}
    ),
    EventState.SETTLED: frozenset({EventState.REFUNDED, EventState.DISPUTED}),
    EventState.REFUNDED: frozenset(),  # Terminal
    EventState.DISPUTED: frozenset({EventState.REFUNDED}),
    EventState.FAILED: frozenset(
        {EventState.AUTHORIZED, EventState.CAPTURED}
    ),  # Retry after failure
}


@dataclass(frozen=True)
class StateVersion:
    """A state observation with the event's causal timestamp."""

    state: EventState
    ts: float
    event_id: str = ""


@dataclass(frozen=True)
class TransitionResult:
    """Result of applying an incoming event to the current state."""

    accepted: bool
    old_state: EventState | None
    new_state: EventState
    reason: str


class OutOfOrderGuard:
    """Protects against events arriving out of order (§11.2, §11.1).

    Test: payment.captured arrives, then payment.authorized arrives later.
    Final state MUST remain CAPTURED, not AUTHORIZED.

    Uses precedence ordering: only allow forward transitions.
    """

    def __init__(self) -> None:
        # entity_id → (current_state, highest_precedence_seen)
        self._states: dict[str, tuple[EventState, int]] = {}
        # entity_id → list of all events received (for audit)
        self._event_log: dict[str, list[StateVersion]] = {}

    def apply(self, entity_id: str, incoming: StateVersion) -> TransitionResult:
        """Apply an incoming event. Only accept if it moves state forward.

        §11.2: `captured` arrives first, then `authorized` arrives later.
        Final state MUST remain CAPTURED.
        """
        # Record every event for audit
        self._event_log.setdefault(entity_id, []).append(incoming)

        current = self._states.get(entity_id)
        incoming_prec = _PRECEDENCE.get(incoming.state, -1)

        if current is None:
            # First event for this entity — accept unconditionally
            self._states[entity_id] = (incoming.state, incoming_prec)
            logger.info(
                "Entity %s: initial state → %s (event: %s)",
                entity_id,
                incoming.state.value,
                incoming.event_id,
            )
            return TransitionResult(
                accepted=True,
                old_state=None,
                new_state=incoming.state,
                reason="initial state",
            )

        current_state, current_prec = current

        # ── Reject backward transitions ───────────────────────────
        if incoming_prec <= current_prec and incoming.state != current_state:
            # Special case: FAILED → higher state is allowed (retry)
            if current_state == EventState.FAILED:
                pass  # Fall through to accept
            else:
                logger.warning(
                    "Entity %s: REJECTED out-of-order event %s "
                    "(current: %s, prec %d vs %d). Final state remains %s.",
                    entity_id,
                    incoming.state.value,
                    current_state.value,
                    incoming_prec,
                    current_prec,
                    current_state.value,
                )
                return TransitionResult(
                    accepted=False,
                    old_state=current_state,
                    new_state=current_state,
                    reason=(
                        f"out-of-order: {incoming.state.value} (prec={incoming_prec}) "
                        f"cannot regress from {current_state.value} (prec={current_prec})"
                    ),
                )

        # ── Duplicate event ───────────────────────────────────────
        if incoming.state == current_state:
            logger.debug(
                "Entity %s: duplicate event %s — no state change",
                entity_id,
                incoming.state.value,
            )
            return TransitionResult(
                accepted=False,
                old_state=current_state,
                new_state=current_state,
                reason=f"duplicate event: already in {current_state.value}",
            )

        # ── Validate transition is allowed ────────────────────────
        allowed = _VALID_TRANSITIONS.get(current_state, frozenset())
        if incoming.state not in allowed:
            logger.warning(
                "Entity %s: invalid transition %s → %s (not in allowed set)",
                entity_id,
                current_state.value,
                incoming.state.value,
            )
            return TransitionResult(
                accepted=False,
                old_state=current_state,
                new_state=current_state,
                reason=(
                    f"invalid transition: {current_state.value} → "
                    f"{incoming.state.value} not in allowed set"
                ),
            )

        # ── Accept forward transition ─────────────────────────────
        self._states[entity_id] = (incoming.state, incoming_prec)
        logger.info(
            "Entity %s: %s → %s (event: %s)",
            entity_id,
            current_state.value,
            incoming.state.value,
            incoming.event_id,
        )
        return TransitionResult(
            accepted=True,
            old_state=current_state,
            new_state=incoming.state,
            reason="forward transition accepted",
        )

    def current_state(self, entity_id: str) -> EventState | None:
        """Get the current state for an entity."""
        entry = self._states.get(entity_id)
        return entry[0] if entry else None

    def event_history(self, entity_id: str) -> list[StateVersion]:
        """Get the full event log for an entity (for audit)."""
        return list(self._event_log.get(entity_id, []))
