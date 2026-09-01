"""Case-level state machine — a custom FSM per §3.3, not a framework."""

from __future__ import annotations

from enum import Enum


class RecoveryState(str, Enum):
    """Recovery case lifecycle states from §3.3 + §3.5."""

    DETECTED = "DETECTED"
    RECONCILING = "RECONCILING"
    CLASSIFIED = "CLASSIFIED"
    RISK_ASSESSED = "RISK_ASSESSED"
    CANDIDATES_GENERATED = "CANDIDATES_GENERATED"
    OPTIMIZED = "OPTIMIZED"
    POLICY_CHECK = "POLICY_CHECK"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    SCHEDULED = "SCHEDULED"
    EXECUTING = "EXECUTING"
    SUCCESS = "SUCCESS"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"
    RECONCILE = "RECONCILE"
    RETRY = "RETRY"
    ESCALATE = "ESCALATE"


TERMINAL_STATES: frozenset[RecoveryState] = frozenset(
    {
        RecoveryState.BLOCKED,
        RecoveryState.RECOVERED,
        RecoveryState.ESCALATE,
    }
)


TRANSITIONS: dict[RecoveryState, frozenset[RecoveryState]] = {
    RecoveryState.DETECTED: frozenset({RecoveryState.RECONCILING}),
    RecoveryState.RECONCILING: frozenset(
        {RecoveryState.CLASSIFIED, RecoveryState.DETECTED}
    ),
    RecoveryState.CLASSIFIED: frozenset({RecoveryState.RISK_ASSESSED}),
    RecoveryState.RISK_ASSESSED: frozenset({RecoveryState.CANDIDATES_GENERATED}),
    RecoveryState.CANDIDATES_GENERATED: frozenset({RecoveryState.OPTIMIZED}),
    RecoveryState.OPTIMIZED: frozenset({RecoveryState.POLICY_CHECK}),
    RecoveryState.POLICY_CHECK: frozenset(
        {RecoveryState.APPROVED, RecoveryState.BLOCKED}
    ),
    RecoveryState.APPROVED: frozenset({RecoveryState.SCHEDULED, RecoveryState.EXECUTING}),
    RecoveryState.BLOCKED: frozenset(),
    RecoveryState.SCHEDULED: frozenset({RecoveryState.EXECUTING}),
    RecoveryState.EXECUTING: frozenset(
        {RecoveryState.SUCCESS, RecoveryState.UNKNOWN, RecoveryState.FAILED}
    ),
    RecoveryState.SUCCESS: frozenset({RecoveryState.RECOVERED}),
    RecoveryState.UNKNOWN: frozenset(
        {RecoveryState.RECONCILE}
    ),  # §3.5: never blindly retry an UNKNOWN
    RecoveryState.FAILED: frozenset(
        {RecoveryState.RETRY, RecoveryState.ESCALATE, RecoveryState.RECONCILE}
    ),
    RecoveryState.RECONCILE: frozenset(
        {RecoveryState.RECOVERED, RecoveryState.RETRY, RecoveryState.FAILED}
    ),
    RecoveryState.RETRY: frozenset({RecoveryState.EXECUTING}),
    RecoveryState.RECOVERED: frozenset(),
    RecoveryState.ESCALATE: frozenset(),
}


class InvalidCaseTransition(ValueError):
    """Raised when a case tries an unsupported state transition."""


class CaseStateMachine:
    """Plain-Python FSM driving the lifecycle of a recovery case."""

    def __init__(
        self, initial: RecoveryState = RecoveryState.DETECTED
    ) -> None:
        if not isinstance(initial, RecoveryState):
            raise ValueError(f"Invalid initial state: {initial!r}")
        self._state = initial

    @property
    def state(self) -> RecoveryState:
        return self._state

    def can_transition(self, target: RecoveryState) -> bool:
        if not isinstance(target, RecoveryState):
            return False
        return target in TRANSITIONS[self._state]

    def transition(self, target: RecoveryState) -> RecoveryState:
        if not isinstance(target, RecoveryState):
            raise InvalidCaseTransition(f"Invalid target state: {target!r}")
        if target not in TRANSITIONS[self._state]:
            raise InvalidCaseTransition(
                f"Cannot transition {self._state.value} -> {target.value}"
            )
        self._state = target
        return self._state

    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    def reset(self, initial: RecoveryState = RecoveryState.DETECTED) -> None:
        self._state = initial