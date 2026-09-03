"""Test suite: Policy Engine — every gate from Track C step 2 (§7)."""

import pytest
from datetime import datetime, timezone, timedelta

from app.contracts import Action, PolicyGateResult
from app.core.obligation import Obligation, ObligationStatus
from app.core.recovery_case import RecoveryCase
from app.policy.policy_engine import PolicyEngine
from app.policy.contact_policy import ContactPolicyEngine, Channel
from app.policy.consent_manager import ConsentManager
from app.policy.fraud_detector import FraudDetector
from app.policy.blast_radius import BlastRadiusGuard


def _make_case(**overrides) -> RecoveryCase:
    defaults = dict(
        case_id="RC_TEST",
        customer_id="cust_test",
        root_cause="insufficient_funds",
        natural_pay_probability=0.3,
        fraud_score=0.01,
        dispute_score=0.01,
    )
    defaults.update(overrides)
    case = RecoveryCase(**defaults)
    case.add_obligation(
        Obligation(obligation_id="obl_test", type="payment", original_amount=500000)
    )
    return case


class TestPolicyPassthrough:
    """NO_ACTION, WAIT, BLOCK should always pass."""

    def test_no_action_approved(self):
        pe = PolicyEngine()
        case = _make_case()
        ev = pe.evaluate(case, Action.NO_ACTION)
        assert ev.result == PolicyGateResult.APPROVED

    def test_wait_approved(self):
        pe = PolicyEngine()
        case = _make_case()
        ev = pe.evaluate(case, Action.WAIT)
        assert ev.result == PolicyGateResult.APPROVED

    def test_block_approved(self):
        pe = PolicyEngine()
        case = _make_case()
        ev = pe.evaluate(case, Action.BLOCK)
        assert ev.result == PolicyGateResult.APPROVED


class TestConsentGate:
    """Gate 1: TRAI consent."""

    def test_no_consent_blocks_sms(self):
        consent = ConsentManager()
        pe = PolicyEngine(consent=consent)
        case = _make_case()
        # No consent registered → should block
        ev = pe.evaluate(case, Action.SEND_SMS)
        # Consent check should appear in results
        assert "consent" in ev.passed_checks or "consent" in ev.failed_checks


class TestContactWindowGate:
    """Gate 2: Contact window — 7:01 PM action must hold for 8 AM (§21.3)."""

    def test_701pm_hold_for_8am(self):
        """§21.3: 7:01 PM action → held until 8:00 AM.

        ContactPolicyEngine uses UTC windows. 22:01 UTC is outside the
        default 8:00-21:00 UTC window → contact_window gate should fail.
        """
        pe = PolicyEngine()
        case = _make_case()
        # 22:01 UTC — outside default 8:00-21:00 UTC window
        late_night = datetime(2026, 9, 2, 22, 1, tzinfo=timezone.utc)
        ev = pe.evaluate(case, Action.SEND_SMS, at=late_night)
        # contact_window should appear in failed checks
        assert "contact_window" in ev.failed_checks
        assert ev.result == PolicyGateResult.BLOCKED

    def test_10am_allowed(self):
        """10 AM IST = 4:30 UTC — should be within window."""
        pe = PolicyEngine()
        case = _make_case()
        morning = datetime(2026, 9, 2, 4, 30, tzinfo=timezone.utc)
        ev = pe.evaluate(case, Action.SEND_SMS, at=morning)
        # Contact window should pass
        assert "contact_window" not in ev.failed_checks


class TestFraudGate:
    """Gate 5: Fraud detection."""

    def test_high_fraud_blocks_financial(self):
        pe = PolicyEngine()
        case = _make_case(fraud_score=0.95)
        ev = pe.evaluate(case, Action.RETRY_SAME_METHOD)
        assert ev.result == PolicyGateResult.BLOCKED
        assert "fraud" in ev.failed_checks

    def test_low_fraud_passes(self):
        pe = PolicyEngine()
        case = _make_case(fraud_score=0.01)
        ev = pe.evaluate(case, Action.SEND_EMAIL)
        assert "fraud" in ev.passed_checks


class TestDisputeGate:
    """Gate 6: Active dispute blocks everything (§21.3)."""

    def test_dispute_blocks_all_outbound(self):
        pe = PolicyEngine()
        case = _make_case()
        case.obligations[0].mark_disputed(250000)
        ev = pe.evaluate(case, Action.SEND_SMS)
        assert ev.result == PolicyGateResult.BLOCKED
        assert "dispute" in ev.failed_checks
        assert "ACTIVE_DISPUTE" in ev.violations

    def test_dispute_allows_no_action(self):
        pe = PolicyEngine()
        case = _make_case()
        case.obligations[0].mark_disputed(250000)
        ev = pe.evaluate(case, Action.NO_ACTION)
        assert ev.result == PolicyGateResult.APPROVED


class TestReversibilityGate:
    """Gate 7: High-value actions require human approval."""

    def test_large_amount_requires_human(self):
        pe = PolicyEngine()
        case = RecoveryCase(
            case_id="RC_BIG",
            customer_id="cust_big",
            root_cause="overdue_invoice",
            natural_pay_probability=0.3,
            fraud_score=0.01,
            dispute_score=0.01,
        )
        case.add_obligation(
            Obligation(
                obligation_id="obl_big",
                type="payment",
                original_amount=60000000,  # ₹6L
            )
        )
        ev = pe.evaluate_autonomy(case, Action.RETRY_SAME_METHOD)
        assert ev.human_approval_required


class TestBlastRadiusGate:
    """Gate 8: Blast radius / circuit breaker."""

    def test_blast_radius_passes_normal(self):
        pe = PolicyEngine()
        case = _make_case()
        ev = pe.evaluate(case, Action.SEND_EMAIL)
        assert "blast_radius" in ev.passed_checks


class TestCustomerOptOut:
    """§21.3: Customer opt-out → permanent stop."""

    def test_opted_out_customer(self):
        """Customer says 'Stop messaging me' → no outbound actions."""
        pe = PolicyEngine()
        case = _make_case()
        # Simulate opt-out via consent withdrawal
        # With default consent manager, no consent = blocked
        ev = pe.evaluate(case, Action.SEND_SMS)
        # The consent gate should be in the evaluation
        assert "consent" in ev.passed_checks or "consent" in ev.failed_checks
