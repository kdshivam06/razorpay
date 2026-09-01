"""Financial Obligation Ledger — the canonical record of money owed."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ObligationStatus(str, Enum):
    """Obligation lifecycle states from §3.1."""

    OPEN = "OPEN"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    RECOVERING = "RECOVERING"
    PTP_HOLD = "PTP_HOLD"
    DISPUTED = "DISPUTED"
    RECOVERED = "RECOVERED"
    WRITTEN_OFF = "WRITTEN_OFF"
    BLOCKED = "BLOCKED"


TERMINAL_STATUSES = frozenset(
    {
        ObligationStatus.RECOVERED,
        ObligationStatus.WRITTEN_OFF,
        ObligationStatus.BLOCKED,
    }
)


class ObligationError(Exception):
    """Base error for obligation-ledger violations."""


class InvalidTransition(ObligationError):
    """Raised when an obligation cannot move to the requested state."""


class DoubleDipPrevented(ObligationError):
    """Raised when a second recovery path or duplicate payment is attempted."""


class AmountError(ObligationError):
    """Raised on negative / over-limit / inconsistent amounts."""


@dataclass
class Obligation:
    """A canonical financial obligation expressed in paise."""

    obligation_id: str
    type: str  # "payment", "invoice", "subscription", "mandate"
    original_amount: int  # paise
    currency: str = "INR"
    customer_id: str = ""
    merchant_id: str = ""
    razorpay_entity_ids: dict[str, str] = field(default_factory=dict)

    paid_amount: int = 0
    refunded_amount: int = 0
    disputed_amount: int = 0
    status: ObligationStatus = ObligationStatus.OPEN
    _recovery_locked: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.original_amount < 0 or self.paid_amount < 0:
            raise AmountError("Amounts cannot be negative")
        if self.refunded_amount < 0 or self.disputed_amount < 0:
            raise AmountError("Amounts cannot be negative")
        if self.paid_amount > self.original_amount:
            raise AmountError("paid_amount cannot exceed original_amount")
        if self.refunded_amount > self.paid_amount:
            raise AmountError("refunded_amount cannot exceed paid_amount")
        if self.disputed_amount > self.remaining_amount:
            raise AmountError("disputed_amount cannot exceed remaining_amount")
        if self.remaining_amount == 0 and self.paid_amount > 0:
            self.status = ObligationStatus.RECOVERED
        elif self.paid_amount > 0:
            self.status = ObligationStatus.PARTIALLY_PAID

    # -- amounts ----------------------------------------------------------

    @property
    def remaining_amount(self) -> int:
        """Amount still owed (paise). Refunds reduce net recovery only."""
        return self.original_amount - self.paid_amount

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    # -- transitions -------------------------------------------------------

    def record_payment(self, amount: int) -> ObligationStatus:
        """Record a payment. Overpayment and terminal-state payments are rejected."""
        if self.is_terminal:
            raise InvalidTransition(
                f"Obligation {self.obligation_id} is {self.status.value}; cannot record payment"
            )
        if amount <= 0:
            raise AmountError(f"Payment must be positive, got {amount}")
        if amount > self.remaining_amount:
            raise DoubleDipPrevented(
                f"Payment {amount} exceeds remaining {self.remaining_amount}; "
                "double-counted or overpayment rejected"
            )
        self.paid_amount += amount
        if self.remaining_amount == 0:
            self.status = ObligationStatus.RECOVERED
            self._recovery_locked = False  # recovered -> all recovery paths cancelled
        elif self.paid_amount > 0:
            self.status = ObligationStatus.PARTIALLY_PAID
        return self.status

    def record_refund(self, amount: int) -> ObligationStatus:
        """Record a refund, capped at the net amount paid so far."""
        if amount <= 0:
            raise AmountError(f"Refund must be positive, got {amount}")
        if amount > self.paid_amount - self.refunded_amount:
            raise AmountError(
                f"Refund {amount} exceeds net paid {self.paid_amount - self.refunded_amount}"
            )
        self.refunded_amount += amount
        return self.status

    def mark_disputed(self, amount: int | None = None) -> ObligationStatus:
        """Move the obligation into DISPUTED for the given amount."""
        if self.is_terminal:
            raise InvalidTransition(
                f"Obligation {self.obligation_id} is {self.status.value}; cannot enter dispute"
            )
        if amount is None:
            amount = self.remaining_amount
        if amount <= 0 or amount > self.remaining_amount:
            raise AmountError(f"Disputed amount {amount} invalid vs remaining {self.remaining_amount}")
        self.disputed_amount = min(self.disputed_amount + amount, self.remaining_amount)
        self.status = ObligationStatus.DISPUTED
        return self.status

    def start_recovery(self) -> ObligationStatus:
        """Enter RECOVERING and grab the single-recovery-path lock."""
        if self.is_terminal:
            raise InvalidTransition(
                f"Obligation {self.obligation_id} is {self.status.value}; cannot start recovery"
            )
        if self._recovery_locked:
            raise DoubleDipPrevented(
                f"Obligation {self.obligation_id} already has an active recovery path"
            )
        self._recovery_locked = True
        self.status = ObligationStatus.RECOVERING
        return self.status

    def create_ptp(self) -> ObligationStatus:
        """Place a recovering obligation on PTP_HOLD (promise to pay)."""
        if self.is_terminal:
            raise InvalidTransition(
                f"Obligation {self.obligation_id} is {self.status.value}; cannot create PTP"
            )
        if self.status is ObligationStatus.OPEN:
            self._recovery_locked = True
        self.status = ObligationStatus.PTP_HOLD
        return self.status

    def resume_after_ptp(self) -> ObligationStatus:
        """Return from PTP_HOLD to an active recovery path."""
        if self.status is not ObligationStatus.PTP_HOLD:
            raise InvalidTransition(
                f"Cannot resume; obligation is {self.status.value}, not PTP_HOLD"
            )
        self.status = ObligationStatus.RECOVERING
        return self.status

    def write_off(self) -> ObligationStatus:
        """Write the obligation off (terminal)."""
        if self.is_terminal:
            raise InvalidTransition(
                f"Obligation {self.obligation_id} is {self.status.value}; cannot write off"
            )
        self._recovery_locked = False
        self.status = ObligationStatus.WRITTEN_OFF
        return self.status

    def block(self) -> ObligationStatus:
        """Hard-block the obligation (terminal, e.g. fraud)."""
        if self.is_terminal:
            raise InvalidTransition(
                f"Obligation {self.obligation_id} is {self.status.value}; cannot block"
            )
        self._recovery_locked = False
        self.status = ObligationStatus.BLOCKED
        return self.status

    # -- recovery lock -----------------------------------------------------

    @property
    def is_recovery_locked(self) -> bool:
        return self._recovery_locked

    def release_recovery_lock(self) -> bool:
        """Manually release the recovery lock without a state change."""
        released = self._recovery_locked
        self._recovery_locked = False
        return released