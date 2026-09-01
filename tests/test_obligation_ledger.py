"""Tests for the obligation ledger — §3.1 transitions, partial payment, double-dip."""

import pytest

from app.core.obligation import (
    AmountError,
    DoubleDipPrevented,
    InvalidTransition,
    Obligation,
    ObligationStatus,
)
from app.core.recovery_lock import RecoveryLock, RecoveryLockError


def make_obl(original: int = 100_000, paid: int = 0, **kwargs) -> Obligation:
    return Obligation(
        obligation_id=kwargs.pop("obligation_id", "OBL_9281"),
        type=kwargs.pop("type", "invoice"),
        original_amount=original,
        paid_amount=paid,
        customer_id=kwargs.pop("customer_id", "cus_1"),
        merchant_id=kwargs.pop("merchant_id", "mer_1"),
        razorpay_entity_ids={"payment_id": "pay_1", "order_id": "order_1"},
        **kwargs,
    )


# --------------------------------------------------------------------------
# Initial state & invariants
# --------------------------------------------------------------------------


class TestInvariants:
    def test_default_status_is_open(self):
        obl = make_obl()
        assert obl.status is ObligationStatus.OPEN
        assert obl.remaining_amount == 100_000

    def test_constructed_partial_payment_gets_partially_paid_status(self):
        obl = make_obl(paid=40_000)
        assert obl.status is ObligationStatus.PARTIALLY_PAID
        assert obl.remaining_amount == 60_000

    def test_constructed_full_payment_gets_recovered_status(self):
        obl = make_obl(paid=100_000)
        assert obl.status is ObligationStatus.RECOVERED
        assert obl.remaining_amount == 0

    def test_negative_original_amount_rejected(self):
        with pytest.raises(AmountError):
            make_obl(original=-1)

    def test_overpaid_construction_rejected(self):
        with pytest.raises(AmountError):
            make_obl(paid=110_000)

    def test_refund_cannot_exceed_paid_on_construction(self):
        with pytest.raises(AmountError):
            make_obl(paid=40_000, refunded_amount=50_000)

    def test_disputed_cannot_exceed_remaining_on_construction(self):
        with pytest.raises(AmountError):
            make_obl(paid=40_000, disputed_amount=70_000)


# --------------------------------------------------------------------------
# §3.1 state transitions
# --------------------------------------------------------------------------


class TestStateTransitions:
    def test_open_to_partially_paid(self):
        obl = make_obl()
        assert obl.record_payment(30_000) is ObligationStatus.PARTIALLY_PAID
        assert obl.paid_amount == 30_000
        assert obl.remaining_amount == 70_000

    def test_partially_paid_to_recovering(self):
        obl = make_obl(paid=30_000)
        assert obl.start_recovery() is ObligationStatus.RECOVERING
        assert obl.is_recovery_locked is True

    def test_recovering_to_ptp_hold(self):
        obl = make_obl(paid=30_000)
        obl.start_recovery()
        assert obl.create_ptp() is ObligationStatus.PTP_HOLD

    def test_ptp_hold_to_disputed(self):
        obl = make_obl(paid=30_000)
        obl.create_ptp()
        assert obl.mark_disputed() is ObligationStatus.DISPUTED
        assert obl.disputed_amount == 70_000

    def test_disputed_to_recovered_when_fully_paid(self):
        obl = make_obl()
        obl.mark_disputed(amount=100_000)
        assert obl.record_payment(100_000) is ObligationStatus.RECOVERED
        assert obl.remaining_amount == 0

    def test_open_to_recovered_in_one_payment(self):
        obl = make_obl()
        assert obl.record_payment(100_000) is ObligationStatus.RECOVERED
        assert obl.paid_amount == 100_000
        assert obl.remaining_amount == 0

    def test_partially_paid_to_recovered(self):
        obl = make_obl(paid=40_000)
        obl.record_payment(30_000)
        assert obl.status is ObligationStatus.PARTIALLY_PAID
        assert obl.record_payment(30_000) is ObligationStatus.RECOVERED
        assert obl.paid_amount == 100_000

    def test_to_written_off(self):
        obl = make_obl(paid=10_000)
        assert obl.write_off() is ObligationStatus.WRITTEN_OFF
        assert obl._recovery_locked is False

    def test_to_blocked(self):
        obl = make_obl()
        assert obl.block() is ObligationStatus.BLOCKED

    def test_recovering_to_blocked(self):
        obl = make_obl()
        obl.start_recovery()
        assert obl.block() is ObligationStatus.BLOCKED

    def test_ptp_hold_resumes_to_recovering(self):
        obl = make_obl()
        obl.create_ptp()
        assert obl.resume_after_ptp() is ObligationStatus.RECOVERING


class TestTerminalStates:
    @pytest.mark.parametrize("status", [ObligationStatus.RECOVERED])
    def test_recovered_is_terminal(self, status):
        obl = make_obl()
        obl.record_payment(100_000)
        assert obl.status is status
        for fn in (
            lambda: obl.record_payment(1),
            lambda: obl.mark_disputed(),
            lambda: obl.start_recovery(),
            lambda: obl.create_ptp(),
            lambda: obl.write_off(),
            lambda: obl.block(),
        ):
            with pytest.raises(InvalidTransition):
                fn()

    def test_written_off_is_terminal(self):
        obl = make_obl()
        obl.write_off()
        with pytest.raises(InvalidTransition):
            obl.record_payment(1)
        with pytest.raises(InvalidTransition):
            obl.start_recovery()

    def test_blocked_is_terminal(self):
        obl = make_obl()
        obl.block()
        with pytest.raises(InvalidTransition):
            obl.record_payment(1)
        with pytest.raises(InvalidTransition):
            obl.start_recovery()


# --------------------------------------------------------------------------
# Partial payments
# --------------------------------------------------------------------------


class TestPartialPayment:
    def test_multiple_partial_payments_accumulate(self):
        obl = make_obl()
        obl.record_payment(10_000)
        obl.record_payment(25_000)
        obl.record_payment(15_000)
        assert obl.status is ObligationStatus.PARTIALLY_PAID
        assert obl.paid_amount == 50_000
        assert obl.remaining_amount == 50_000

    def test_partial_payment_keeps_recovery_lock(self):
        obl = make_obl()
        obl.start_recovery()
        obl.record_payment(40_000)
        assert obl.status is ObligationStatus.PARTIALLY_PAID
        assert obl.is_recovery_locked is True

    def test_partial_payment_during_ptp_hold(self):
        obl = make_obl()
        obl.create_ptp()
        obl.record_payment(40_000)
        assert obl.status is ObligationStatus.PARTIALLY_PAID
        assert obl.remaining_amount == 60_000

    def test_zero_or_negative_payment_rejected(self):
        obl = make_obl()
        with pytest.raises(AmountError):
            obl.record_payment(0)
        with pytest.raises(AmountError):
            obl.record_payment(-100)

    def test_refund_reduces_net_recovery(self):
        obl = make_obl()
        obl.record_payment(60_000)
        obl.record_refund(10_000)
        assert obl.refunded_amount == 10_000
        assert obl.remaining_amount == 40_000
        with pytest.raises(AmountError):
            obl.record_refund(60_000)  # exceeds net paid


# --------------------------------------------------------------------------
# Double-dip prevention (§3.4)
# --------------------------------------------------------------------------


class TestDoubleDipPrevention:
    def test_overpayment_rejected(self):
        obl = make_obl()
        obl.record_payment(60_000)
        with pytest.raises(DoubleDipPrevented):
            obl.record_payment(50_000)  # > remaining 40_000

    def test_exact_remaining_is_not_double_dip(self):
        obl = make_obl()
        obl.record_payment(60_000)
        obl.record_payment(40_000)
        assert obl.status is ObligationStatus.RECOVERED

    def test_second_recovery_path_rejected(self):
        obl = make_obl()
        obl.start_recovery()
        with pytest.raises(DoubleDipPrevented):
            obl.start_recovery()

    def test_recovery_lock_registry_single_holder(self):
        lock = RecoveryLock()
        assert lock.acquire("OBL_1", holder="payment_link") is True
        assert lock.acquire("OBL_1", holder="sms_retry") is False
        assert lock.is_held("OBL_1")
        assert lock.holder("OBL_1") == "payment_link"
        assert lock.release("OBL_1", holder="payment_link") is True
        assert lock.is_held("OBL_1") is False

    def test_recovered_cancels_other_recovery_paths(self):
        lock = RecoveryLock()
        assert lock.acquire("OBL_1", holder="mandate_retry") is True
        assert lock.acquire("OBL_1", holder="payment_link") is False

        obl = make_obl(obligation_id="OBL_1")
        obl.start_recovery()
        obl.record_payment(100_000)  # customer pays manually -> RECOVERED
        assert obl.status is ObligationStatus.RECOVERED
        assert obl.is_recovery_locked is False

        cancelled = lock.release_all("OBL_1")
        assert cancelled == ["mandate_retry"]  # every remaining path -> CANCELLED

    def test_recovered_clears_obligation_recovery_lock(self):
        obl = make_obl()
        obl.start_recovery()
        obl.record_payment(100_000)
        assert obl.status is ObligationStatus.RECOVERED
        assert obl.is_recovery_locked is False

    def test_manual_release_of_recovery_lock(self):
        obl = make_obl()
        obl.start_recovery()
        assert obl.release_recovery_lock() is True
        assert obl.release_recovery_lock() is False
        assert obl.start_recovery() is ObligationStatus.RECOVERING

    def test_wrong_holder_cannot_release_lock(self):
        lock = RecoveryLock()
        lock.acquire("OBL_1", holder="voice")
        with pytest.raises(RecoveryLockError):
            lock.release("OBL_1", holder="sms")