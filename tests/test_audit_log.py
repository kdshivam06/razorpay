"""Test suite: Audit log — hash-chain integrity (§13.2, §21.3)."""

import pytest

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.audit.pii_masker import PiiMasker
from app.audit.version_tracker import VersionTracker


class TestHashChainIntegrity:
    """§21.3: Hash chain immutability — if an earlier record is modified, the chain breaks."""

    def test_empty_chain_verifies(self):
        al = AuditLogger()
        errors = al.verify_chain()
        assert len(errors) == 0

    def test_single_entry_verifies(self):
        al = AuditLogger()
        al.append(trigger_type="TEST", payload={"data": "test"})
        errors = al.verify_chain()
        assert len(errors) == 0

    def test_multi_entry_chain_verifies(self):
        al = AuditLogger()
        al.append(
            trigger_type="CASE_CREATED", payload={"case": "RC_001"}, case_id="RC_001"
        )
        al.append(
            trigger_type="ACTION_PROPOSED", payload={"action": "SMS"}, case_id="RC_001"
        )
        al.append(
            trigger_type="POLICY_APPROVED", payload={"gate": "passed"}, case_id="RC_001"
        )
        al.append(
            trigger_type="ACTION_EXECUTED",
            payload={"result": "SUCCESS"},
            case_id="RC_001",
        )
        errors = al.verify_chain()
        assert len(errors) == 0
        assert al.chain_length == 4

    def test_tampered_entry_detected(self):
        """Modify an entry's hash → chain should break."""
        al = AuditLogger()
        al.append(trigger_type="CASE_CREATED", payload={"case": "RC_001"})
        al.append(trigger_type="ACTION_PROPOSED", payload={"action": "SMS"})
        al.append(trigger_type="POLICY_APPROVED", payload={"gate": "passed"})

        # Tamper with the middle entry
        import dataclasses

        tampered = dataclasses.replace(al._chain[1], current_hash="0" * 64)
        al._chain[1] = tampered

        errors = al.verify_chain()
        assert len(errors) > 0  # Chain should be broken

    def test_entries_for_case(self):
        al = AuditLogger()
        al.append(trigger_type="A", payload={"x": 1}, case_id="RC_001")
        al.append(trigger_type="B", payload={"x": 2}, case_id="RC_002")
        al.append(trigger_type="C", payload={"x": 3}, case_id="RC_001")
        entries = al.entries_for_case("RC_001")
        assert len(entries) == 2
        assert all(e.case_id == "RC_001" for e in entries)


class TestDecisionTracer:
    """Decision trace captures the full §1.2 record."""

    def test_build_trace(self):
        from app.contracts import Action, CandidateAction

        tracer = DecisionTracer()
        trace = tracer.build(
            case_id="RC_001",
            trigger_event="payment.failed",
            state="OPEN",
            root_cause="insufficient_funds",
            natural_payment_probability=0.30,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=500000,
            candidate_actions=[],
            selected_action=Action.SEND_SMS,
            selected_economic_score=1234.56,
            rejected_actions={"EMAIL": "Lower score"},
            policy_checks_passed=["consent", "contact_window"],
            policy_checks_failed=[],
            policy_gate_result="APPROVED",
        )
        assert trace.case_id == "RC_001"
        assert trace.selected_action == Action.SEND_SMS
        assert trace.policy_gate_result == "APPROVED"

    def test_latest_trace(self):
        tracer = DecisionTracer()
        from app.contracts import Action

        tracer.build(
            case_id="RC_001",
            trigger_event="evt_1",
            state="OPEN",
            root_cause="test",
            natural_payment_probability=0.3,
            uplift_segment="P",
            revenue_at_risk_paise=100,
            candidate_actions=[],
            selected_action=Action.SEND_SMS,
        )
        tracer.build(
            case_id="RC_001",
            trigger_event="evt_2",
            state="RECOVERING",
            root_cause="test",
            natural_payment_probability=0.3,
            uplift_segment="P",
            revenue_at_risk_paise=100,
            candidate_actions=[],
            selected_action=Action.SEND_EMAIL,
        )
        latest = tracer.latest_trace("RC_001")
        assert latest.selected_action == Action.SEND_EMAIL


class TestPreventionLog:
    """Prevention log records every suppressed/blocked action."""

    def test_log_and_summary(self):
        pl = PreventionLog()
        pl.log("RC_001", "SEND_SMS", "Duplicate SMS in cooldown window")
        pl.log("RC_001", "RETRY_SAME_METHOD", "Retry after payment prevented")
        pl.log("RC_002", "VOICE_CALL", "Outside contact window")
        summary = pl.summary()
        assert summary["total_preventions"] == 3

    def test_for_case(self):
        pl = PreventionLog()
        pl.log("RC_001", "SMS", "duplicate")
        pl.log("RC_002", "SMS", "cooldown")
        assert len(pl.for_case("RC_001")) == 1


class TestPiiMasker:
    """PII masking for Indian data."""

    def test_mask_phone(self):
        m = PiiMasker()
        assert m.mask_phone("+919876543210")[-4:] == "3210"
        assert "*" in m.mask_phone("+919876543210")

    def test_mask_email(self):
        m = PiiMasker()
        masked = m.mask_email("user@example.com")
        assert masked.startswith("u")
        assert "@example.com" in masked
        assert "*" in masked

    def test_mask_pan(self):
        m = PiiMasker()
        masked = m.mask_pan("ABCDE1234F")
        assert masked.startswith("AB")
        assert "*" in masked


class TestVersionTracker:
    """Version tracking for model/policy decisions."""

    def test_current_snapshot(self):
        vt = VersionTracker()
        snap = vt.current()
        assert snap.classifier_version
        assert snap.policy_version

    def test_bump(self):
        vt = VersionTracker()
        vt.bump("classifier", "classifier_v2.0")
        snap = vt.current()
        assert snap.classifier_version == "classifier_v2.0"

    def test_snapshot_with_overrides(self):
        vt = VersionTracker()
        snap = vt.snapshot_for_decision(policy_version="policy_v2.0")
        assert snap.policy_version == "policy_v2.0"
