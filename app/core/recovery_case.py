"""Recovery Case abstraction — wraps obligations and the decision trace (§3.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.obligation import Obligation
from app.core.state_machine import CaseStateMachine, RecoveryState


class UpliftSegment(str, Enum):
    """Customer uplift segments from §5.4."""

    SURE_THING = "SURE_THING"
    PERSUADABLE = "PERSUADABLE"
    LOST_CAUSE = "LOST_CAUSE"
    SLEEPING_DOG = "SLEEPING_DOG"


@dataclass
class RecoveryCase:
    """One recovery interaction across one customer and ≥1 obligations."""

    case_id: str
    customer_id: str
    obligations: list[Obligation] = field(default_factory=list)

    root_cause: str = ""
    fraud_score: float = 0.0
    dispute_score: float = 0.0
    natural_pay_probability: float = 0.0
    best_action: str = ""
    uplift_segment: UpliftSegment | str = ""

    communications: list[dict] = field(default_factory=list)
    payment_links: list[dict] = field(default_factory=list)
    ptps: list[dict] = field(default_factory=list)

    decisions: list[dict] = field(default_factory=list)
    audit_events: list[dict] = field(default_factory=list)

    recovery_lock: bool = False
    state_machine: CaseStateMachine = field(
        default_factory=CaseStateMachine, repr=False
    )

    def __post_init__(self) -> None:
        self.fraud_score = max(0.0, min(1.0, self.fraud_score))
        self.dispute_score = max(0.0, min(1.0, self.dispute_score))
        self.natural_pay_probability = max(0.0, min(1.0, self.natural_pay_probability))

    # -- state machine delegate --------------------------------------------

    @property
    def state(self) -> RecoveryState:
        return self.state_machine.state

    def transition(self, target: RecoveryState) -> RecoveryState:
        return self.state_machine.transition(target)

    # -- obligations ---------------------------------------------------------

    def add_obligation(self, obligation: Obligation) -> None:
        if any(o.obligation_id == obligation.obligation_id for o in self.obligations):
            raise ValueError(
                f"Obligation {obligation.obligation_id} already on this case"
            )
        self.obligations.append(obligation)

    def total_remaining(self) -> int:
        return sum(o.remaining_amount for o in self.obligations)

    # -- recovery lock ----------------------------------------------------------

    def lock_recovery(self) -> bool:
        """Acquire the case-level recovery lock; only one path at a time."""
        if self.recovery_lock:
            return False
        self.recovery_lock = True
        return True

    def unlock_recovery(self) -> bool:
        was_locked = self.recovery_lock
        self.recovery_lock = False
        return was_locked

    # -- trace helpers ----------------------------------------------------------

    def add_communication(self, entry: dict) -> None:
        self.communications.append(entry)

    def add_payment_link(self, entry: dict) -> None:
        self.payment_links.append(entry)

    def add_ptp(self, entry: dict) -> None:
        self.ptps.append(entry)

    def add_decision(self, decision: dict) -> None:
        self.decisions.append(decision)

    def add_audit_event(self, event: dict) -> None:
        self.audit_events.append(event)