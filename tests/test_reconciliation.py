"""Test suite: Reconciliation — pre-action check, UNKNOWN handling, out-of-order (§11, §21.3)."""

import pytest
import time

from app.contracts import Action, ExecutionState
from app.core.obligation import Obligation, ObligationStatus
from app.core.recovery_case import RecoveryCase
from app.reconciliation.pre_action_check import PreActionReconciler, PreActionVerdict
from app.reconciliation.unknown_state_handler import (
    UnknownStateHandler,
    UnknownResolution,
)
from app.reconciliation.out_of_order import OutOfOrderGuard, EventState, StateVersion
from app.reconciliation.reconciliation_engine import ReconciliationEngine


def _make_case(**overrides) -> RecoveryCase:
    defaults = dict(
        case_id="RC_RECON",
        customer_id="cust_recon",
        root_cause="insufficient_funds",
        natural_pay_probability=0.3,
        fraud_score=0.01,
        dispute_score=0.01,
    )
    defaults.update(overrides)
    case = RecoveryCase(**defaults)
    case.add_obligation(
        Obligation(obligation_id="obl_recon", type="payment", original_amount=100000)
    )
    return case


class TestPreActionCheck:
    """§21.3: Race condition — Agent detects paid, cancels action."""

    def test_already_paid_cancels_action(self):
        """§21.3: 'Already paid' records in data → agent cancels action."""
        case = _make_case()
        case.obligations[0].record_payment(100000)  # Fully paid
        reconciler = PreActionReconciler()
        verdict = reconciler.check(case, Action.SEND_SMS)
        assert not verdict.allow
        assert "paid" in verdict.reason.lower() or "recovered" in verdict.reason.lower()

    def test_dispute_active_cancels_action(self):
        case = _make_case()
        case.obligations[0].mark_disputed(50000)
        reconciler = PreActionReconciler()
        verdict = reconciler.check(case, Action.SEND_SMS)
        assert not verdict.allow
        assert "dispute" in verdict.reason.lower()

    def test_open_case_allows_action(self):
        case = _make_case()
        reconciler = PreActionReconciler()
        verdict = reconciler.check(case, Action.SEND_SMS)
        assert verdict.allow


class TestOutOfOrder:
    """§21.3: Out-of-order — correct state preserved."""

    def test_captured_before_authorized(self):
        """§21.3: `captured` arrives first, then late `authorized` → state stays CAPTURED."""
        guard = OutOfOrderGuard()

        # CAPTURED arrives first
        r1 = guard.apply(
            "pay_123", StateVersion(EventState.CAPTURED, time.time(), "evt_1")
        )
        assert r1.accepted
        assert r1.new_state == EventState.CAPTURED

        # Late AUTHORIZED arrives — MUST NOT regress
        r2 = guard.apply(
            "pay_123", StateVersion(EventState.AUTHORIZED, time.time(), "evt_2")
        )
        assert not r2.accepted  # REJECTED — can't go backwards
        assert r2.new_state == EventState.CAPTURED  # State preserved

        # Final state is still CAPTURED
        assert guard.current_state("pay_123") == EventState.CAPTURED

    def test_forward_transition_accepted(self):
        guard = OutOfOrderGuard()
        r1 = guard.apply(
            "pay_456", StateVersion(EventState.AUTHORIZED, time.time(), "evt_1")
        )
        assert r1.accepted
        r2 = guard.apply(
            "pay_456", StateVersion(EventState.CAPTURED, time.time(), "evt_2")
        )
        assert r2.accepted
        assert guard.current_state("pay_456") == EventState.CAPTURED

    def test_duplicate_event_rejected(self):
        guard = OutOfOrderGuard()
        guard.apply("pay_789", StateVersion(EventState.CAPTURED, time.time(), "evt_1"))
        r2 = guard.apply(
            "pay_789", StateVersion(EventState.CAPTURED, time.time(), "evt_2")
        )
        assert not r2.accepted  # Same state = no transition


class TestUnknownStateHandler:
    """§21.3: API timeout → UNKNOWN → reconcile (never blind retry)."""

    def test_register_and_resolve_no_match(self):
        """Timeout → register UNKNOWN → resolve with no matching resources → stays UNKNOWN."""
        handler = UnknownStateHandler()
        handler.register_unknown(
            case_id="RC_TIMEOUT",
            action=Action.SEND_PAYMENT_LINK,
            idempotency_key="idem_timeout_001",
        )
        assert len(handler.pending_unknowns("RC_TIMEOUT")) == 1

        # Resolve with no existing resources → UNKNOWN (safe to retry)
        resolution = handler.resolve_unknown(
            case_id="RC_TIMEOUT",
            action=Action.SEND_PAYMENT_LINK,
            idempotency_key="idem_timeout_001",
            existing_resources=[],
        )
        assert resolution.actual_state == ExecutionState.UNKNOWN
        assert not resolution.duplicate_effect_found

    def test_resolve_finds_duplicate(self):
        """If matching resource found → SUCCESS, no retry."""
        handler = UnknownStateHandler()
        handler.register_unknown(
            case_id="RC_DUP",
            action=Action.SEND_PAYMENT_LINK,
            idempotency_key="idem_dup_001",
        )

        # Razorpay has a matching payment link
        existing = [
            {
                "id": "plink_found_001",
                "status": "created",
                "notes": {"idempotency_key": "idem_dup_001"},
            }
        ]
        resolution = handler.resolve_unknown(
            case_id="RC_DUP",
            action=Action.SEND_PAYMENT_LINK,
            idempotency_key="idem_dup_001",
            existing_resources=existing,
        )
        assert resolution.actual_state == ExecutionState.SUCCESS
        assert resolution.duplicate_effect_found
        assert resolution.external_ref == "plink_found_001"


class TestReconciliationEngine:
    """Reconciliation engine mismatch detection."""

    def test_detects_status_mismatch(self):
        engine = ReconciliationEngine()
        case = _make_case()
        # Internal says OPEN, Razorpay says CAPTURED
        rz_state = {"obl_recon": {"status": "CAPTURED"}}
        report = engine.reconcile_case(case, razorpay_state=rz_state)
        assert len(report.mismatches) > 0

    def test_clean_reconciliation(self):
        engine = ReconciliationEngine()
        case = _make_case()
        report = engine.reconcile_case(case)
        assert len(report.mismatches) == 0
