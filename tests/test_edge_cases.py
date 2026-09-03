"""Test suite: Edge cases (§21.3) — race condition, partial payment, concurrent pipeline."""

import pytest

from app.contracts import Action, PolicyGateResult
from app.core.obligation import (
    Obligation,
    ObligationStatus,
    DoubleDipPrevented,
    InvalidTransition,
    AmountError,
)
from app.core.recovery_case import RecoveryCase


def _make_case(**overrides) -> RecoveryCase:
    defaults = dict(
        case_id="RC_EDGE",
        customer_id="cust_edge",
        root_cause="insufficient_funds",
        natural_pay_probability=0.3,
        fraud_score=0.01,
        dispute_score=0.01,
    )
    defaults.update(overrides)
    case = RecoveryCase(**defaults)
    case.add_obligation(
        Obligation(
            obligation_id="obl_edge",
            type="payment",
            original_amount=100000,
        )
    )
    return case


class TestPartialPayment:
    """§21.3: Partial payment — remaining tracked correctly."""

    def test_partial_payment_tracking(self):
        """₹40K of ₹1L invoice → remaining = ₹60K."""
        obl = Obligation(
            obligation_id="obl_partial", type="payment", original_amount=10000000
        )
        obl.record_payment(4000000)  # ₹40K
        assert obl.remaining_amount == 6000000  # ₹60K
        assert obl.status == ObligationStatus.PARTIALLY_PAID

    def test_full_payment_recovers(self):
        obl = Obligation(
            obligation_id="obl_full", type="payment", original_amount=10000000
        )
        obl.record_payment(10000000)
        assert obl.remaining_amount == 0
        assert obl.status == ObligationStatus.RECOVERED

    def test_overpayment_prevented(self):
        """Double-dip prevention: can't pay more than remaining."""
        obl = Obligation(
            obligation_id="obl_over", type="payment", original_amount=10000000
        )
        obl.record_payment(6000000)
        with pytest.raises(DoubleDipPrevented):
            obl.record_payment(5000000)  # 6M + 5M > 10M


class TestRecoveryLock:
    """Concurrent pipeline — deduplicated via recovery lock."""

    def test_double_recovery_prevented(self):
        """§21.3: Concurrent pipeline → deduplicated."""
        obl = Obligation(
            obligation_id="obl_lock", type="payment", original_amount=500000
        )
        obl.start_recovery()
        assert obl.is_recovery_locked
        with pytest.raises(DoubleDipPrevented):
            obl.start_recovery()  # Second path blocked

    def test_recovery_after_payment(self):
        """Can't start recovery on a recovered obligation."""
        obl = Obligation(
            obligation_id="obl_recovered", type="payment", original_amount=500000
        )
        obl.record_payment(500000)
        assert obl.is_terminal
        with pytest.raises(InvalidTransition):
            obl.start_recovery()


class TestStateTransitions:
    """Obligation state machine edge cases."""

    def test_negative_payment_rejected(self):
        obl = Obligation(
            obligation_id="obl_neg", type="payment", original_amount=500000
        )
        with pytest.raises(AmountError):
            obl.record_payment(-100)

    def test_terminal_state_immutable(self):
        obl = Obligation(
            obligation_id="obl_term", type="payment", original_amount=500000
        )
        obl.write_off()
        with pytest.raises(InvalidTransition):
            obl.record_payment(100000)

    def test_ptp_hold_and_resume(self):
        obl = Obligation(
            obligation_id="obl_ptp", type="payment", original_amount=500000
        )
        obl.create_ptp()
        assert obl.status == ObligationStatus.PTP_HOLD
        obl.resume_after_ptp()
        assert obl.status == ObligationStatus.RECOVERING

    def test_case_total_remaining(self):
        case = _make_case()
        assert case.total_remaining() == 100000
        case.obligations[0].record_payment(40000)
        assert case.total_remaining() == 60000
