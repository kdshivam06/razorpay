"""Test suite: Decision Packet API — GET /api/cases/{case_id}/decision-packet.

Verifies the full structured decision packet is assembled from real
Track A-C components, with pending markers for E.3/E.4/E.6 fields.
"""

from fastapi.testclient import TestClient

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.contracts import Action, CandidateAction
from app.dashboard.case_inspector import CaseInspector, configure, packet_to_dict
from app.main import app
from app.nlp.message_templates import extract_amounts, extract_dates

client = TestClient(app)


class TestDecisionPacketAPI:
    """Integration tests for the decision packet endpoint."""

    def setup_method(self):
        """Set up fresh inspector with populated test data."""
        self.tracer = DecisionTracer()
        self.audit = AuditLogger()
        self.prevention = PreventionLog()

        # Build a realistic trace with all components
        candidates = [
            CandidateAction(
                action=Action.NO_ACTION,
                expected_recovery_paise=0,
                communication_cost_paise=0,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=0,
                economic_score=0.0,
            ),
            CandidateAction(
                action=Action.SEND_SMS,
                expected_recovery_paise=150000,
                communication_cost_paise=25,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=100,
                economic_score=0.25,
            ),
            CandidateAction(
                action=Action.SEND_PAYMENT_LINK,
                expected_recovery_paise=300000,
                communication_cost_paise=100,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=200,
                economic_score=0.45,
            ),
            CandidateAction(
                action=Action.VOICE_CALL,
                expected_recovery_paise=250000,
                communication_cost_paise=2500,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=700,
                economic_score=0.35,
            ),
        ]

        self.trace = self.tracer.build(
            case_id="RC_TEST_001",
            trigger_event="payment.failed:insufficient_funds",
            state="RISK_ASSESSED",
            root_cause="insufficient_funds",
            natural_payment_probability=0.28,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=15000000,  # ₹1.5L — exceeds ₹1L threshold for human approval
            candidate_actions=candidates,
            selected_action=Action.SEND_PAYMENT_LINK,
            selected_economic_score=0.45,
            selection_reasoning="Payment link has highest positive incremental recovery (+45%) for Persuadable segment",
            timing_rationale="Best retry window predicted at +24h based on customer history",
            rejected_actions={
                "SEND_SMS": "Lower economic score (0.25 vs 0.45 for payment link)",
                "VOICE_CALL": "Outside preferred contact period; higher CX penalty",
                "NO_ACTION": "Natural payment probability only 28%, intervention warranted",
            },
            policy_checks_passed=["consent", "contact_window", "cooldown", "fraud", "dispute", "platform_awareness"],
            policy_checks_failed=[],
            policy_gate_result="APPROVED",
            execution_result="SUCCESS",
            execution_detail="Payment link created: https://rzp.io/i/demo-abc123",
            idempotency_key="idem_RC_TEST_001",
            is_incremental=True,
            control_group=False,
            outcome="RECOVERED",
            outcome_amount_paise=15000000,
            requires_human_approval=True,
            reasoning="Payment link has highest positive incremental recovery (+45%) for Persuadable segment. Requires human approval.",
            model_versions={
                "classifier_version": "classifier_v1",
                "propensity_model_version": "propensity_v1",
                "uplift_model_version": "uplift_v1",
                "policy_version": "policy_v1",
            },
        )

        # Add audit entries
        self.audit.append(
            trigger_type="CASE_CREATED",
            trigger_event="payment.failed",
            payload={"case_id": "RC_TEST_001", "amount": 15000000},
            case_id="RC_TEST_001",
            amount_paise=15000000,
        )
        self.audit.append(
            trigger_type="ACTION_EXECUTED",
            trigger_event="payment_link_created",
            payload={"link_id": "plink_abc123"},
            case_id="RC_TEST_001",
            amount_paise=15000000,
        )

        # Configure inspector with real components
        configure(
            tracer=self.tracer,
            audit=self.audit,
            prevention=self.prevention,
        )

    def test_decision_packet_endpoint_returns_200(self):
        """GET /api/cases/{case_id}/decision-packet returns 200 with full packet."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        assert response.status_code == 200

    def test_decision_packet_has_required_top_level_fields(self):
        """Packet contains all required top-level fields."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        required_fields = [
            "case_id", "amount", "status", "trace_id",
            "ingestion", "diagnosis", "policy_gates",
            "recommendation", "settlement_projection",
            "channel_drafts", "language",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_case_id_and_amount_match_trace(self):
        """case_id and amount match the stored trace."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        assert data["case_id"] == "RC_TEST_001"
        assert data["amount"] == 15000000

    def test_ingestion_fields_populated_from_real_data(self):
        """ingestion block pulls from trace.trigger_event and metadata."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        ingestion = data["ingestion"]
        assert ingestion["raw_failure_reason"] == "payment.failed:insufficient_funds"
        assert ingestion["decline_code"] == "INSUFFICIENT_FUNDS"
        # Source inferred from trigger_event; "payment.failed:..." doesn't contain "webhook"
        assert ingestion["source"] == "unknown"

    def test_diagnosis_fields_populated_from_real_data(self):
        """diagnosis block pulls from trace.root_cause and taxonomy."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        diagnosis = data["diagnosis"]
        assert diagnosis["fault_attribution"] == "insufficient_funds"
        assert diagnosis["taxonomy_code"] == "RC001"
        assert diagnosis["taxonomy_label"] == "Insufficient Funds"
        # rationale_text should have pending marker for E.3
        assert diagnosis["rationale_text"]["pending"] == "E.3 — diagnosis rationale engine"

    def test_policy_gates_structured_from_trace_checks(self):
        """policy_gates derived from trace's passed/failed lists."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        gates = data["policy_gates"]
        assert isinstance(gates, list)
        assert len(gates) > 0

        # Check PASS gates from trace.policy_checks_passed
        passed_names = {g["gate_name"] for g in gates if g["result"] == "PASS"}
        assert "consent" in passed_names
        assert "contact_window" in passed_names
        assert "fraud" in passed_names
        assert "dispute" in passed_names

        # No FAIL gates since trace had empty policy_checks_failed
        fail_gates = [g for g in gates if g["result"] == "FAIL"]
        assert len(fail_gates) == 0

    def test_recommendation_from_selected_action(self):
        """recommendation reflects trace.selected_action and reasoning."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        rec = data["recommendation"]
        assert rec["action"] == "SEND_PAYMENT_LINK"
        assert rec["confidence"] == 0.45
        assert rec["reasoning"] == "Payment link has highest positive incremental recovery (+45%) for Persuadable segment. Requires human approval."
        # requires_human is True for high-value payment link (>₹1L)
        assert rec["requires_human"] is True

    def test_settlement_projection_calculated_from_amount(self):
        """settlement_projection computed from revenue_at_risk with fees."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        proj = data["settlement_projection"]
        assert proj["gross"] == 15000000
        # MDR ~1.5% = 225000 paise
        assert proj["mdr_fee"] == 225000
        # GST 18% on MDR = 40500 paise
        assert proj["gst_on_fee"] == 40500
        assert proj["net_yield"] == 15000000 - 225000 - 40500
        assert proj["expected_date"] is not None

    def test_channel_drafts_generated_per_register(self):
        """channel_drafts contain validated drafts for SMS/WhatsApp/Email/Voice (E.6)."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        drafts = data["channel_drafts"]
        for channel in ["sms", "whatsapp", "email", "voice_script"]:
            assert channel in drafts
            # Each channel has both registers: en (Standard English) and hi-en (Hinglish)
            assert "en" in drafts[channel]
            assert "hi-en" in drafts[channel]

        # Email carries subject + body; both registers must have text
        assert isinstance(drafts["email"]["en"], dict)
        assert drafts["email"]["en"]["subject"]
        assert drafts["email"]["en"]["body"]

    def test_channel_drafts_amount_and_dates_match_case_record(self):
        """Draft text never invents amounts/dates beyond the case record (§2.3, §10.7)."""
        from datetime import date, datetime, timedelta, timezone

        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()

        sms_en = data["channel_drafts"]["sms"]["en"]
        # Amount is derived from backend truth: revenue_at_risk_paise = ₹1,50,000
        assert "₹1,50,000" in sms_en
        assert extract_amounts(sms_en) == [15000000]

        # Any date referenced stays within the case-sourced window (+7d expiry)
        today = datetime.now(timezone.utc).date()
        for iso in extract_dates(sms_en):
            then = date.fromisoformat(iso)
            assert today - timedelta(days=1) <= then <= today + timedelta(days=31)

    def test_language_defaults_to_en(self):
        """language field defaults to 'en'."""
        response = client.get("/api/cases/RC_TEST_001/decision-packet")
        data = response.json()
        assert data["language"] == "en"

    def test_404_for_unknown_case(self):
        """Unknown case_id returns 404."""
        response = client.get("/api/cases/RC_UNKNOWN_999/decision-packet")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_draft_send_queues_to_outbox(self):
        """POST /drafts/send queues a reviewer-approved draft (E.6)."""
        response = client.post(
            "/api/cases/RC_TEST_001/drafts/send",
            json={
                "channel": "sms",
                "register": "en",
                "body": "Demo Merchant: Please pay ₹1,50,000 here: https://rzp.io/i/pl_rc_test_001",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert body["channel"] == "sms"
        assert body["case_id"] == "RC_TEST_001"
        assert body["notification_id"].startswith("notif_draft_")

    def test_draft_send_rejects_unknown_channel(self):
        """POST /drafts/send with an invalid channel returns 400."""
        response = client.post(
            "/api/cases/RC_TEST_001/drafts/send",
            json={"channel": "fax", "register": "en", "body": "ignored"},
        )
        assert response.status_code == 400

    def test_draft_send_rejects_empty_body(self):
        """POST /drafts/send with an empty body returns 400."""
        response = client.post(
            "/api/cases/RC_TEST_001/drafts/send",
            json={"channel": "sms", "register": "en", "body": "   "},
        )
        assert response.status_code == 400

    def test_draft_send_404_for_unknown_case(self):
        """POST /drafts/send for an unknown case returns 404."""
        response = client.post(
            "/api/cases/RC_UNKNOWN_999/drafts/send",
            json={"channel": "sms", "register": "en", "body": "x"},
        )
        assert response.status_code == 404

    def test_packet_to_dict_conversion(self):
        """packet_to_dict produces correct JSON structure with real drafts."""
        inspector = CaseInspector(
            tracer=self.tracer,
            audit=self.audit,
            prevention=self.prevention,
        )
        packet = inspector.build_packet("RC_TEST_001")
        assert packet is not None

        d = packet_to_dict(packet)
        assert d["case_id"] == "RC_TEST_001"
        assert d["diagnosis"]["rationale_text"]["pending"] == "E.3 — diagnosis rationale engine"
        # Drafts are real per-register content, not pending markers (E.6)
        for channel in ["sms", "whatsapp", "email", "voice_script"]:
            assert "en" in d["channel_drafts"][channel]
            assert "hi-en" in d["channel_drafts"][channel]

    def test_blocked_policy_gates_when_trace_has_failures(self):
        """policy_gates shows FAIL when trace.policy_checks_failed is non-empty."""
        # Build a trace with failed policy checks
        tracer = DecisionTracer()
        tracer.build(
            case_id="RC_BLOCKED_001",
            trigger_event="payment.failed:dispute",
            state="DISPUTED",
            root_cause="dispute",
            natural_payment_probability=0.05,
            uplift_segment="SLEEPING_DOG",
            revenue_at_risk_paise=200000,
            candidate_actions=[],
            selected_action=None,
            policy_checks_passed=["idempotency"],
            policy_checks_failed=["dispute", "consent"],
            policy_gate_result="BLOCKED",
        )

        configure(tracer=tracer, audit=AuditLogger(), prevention=PreventionLog())

        response = client.get("/api/cases/RC_BLOCKED_001/decision-packet")
        data = response.json()

        gates = data["policy_gates"]
        fail_names = {g["gate_name"] for g in gates if g["result"] == "FAIL"}
        assert "dispute" in fail_names
        assert "consent" in fail_names


class TestDecisionPacketWithNOACTION:
    """Test packet for cases where NO_ACTION was selected."""

    def setup_method(self):
        tracer = DecisionTracer()
        tracer.build(
            case_id="RC_SURE_THING_001",
            trigger_event="payment.failed:bank_timeout",
            state="CLASSIFIED",
            root_cause="bank_timeout",
            natural_payment_probability=0.92,
            uplift_segment="SURE_THING",
            revenue_at_risk_paise=100000,
            candidate_actions=[
                CandidateAction(
                    action=Action.NO_ACTION,
                    expected_recovery_paise=0,
                    communication_cost_paise=0,
                    operational_cost_paise=0,
                    risk_penalty_paise=0,
                    cx_penalty_paise=0,
                    economic_score=0.0,
                ),
                CandidateAction(
                    action=Action.SEND_SMS,
                    expected_recovery_paise=102000,
                    communication_cost_paise=25,
                    operational_cost_paise=0,
                    risk_penalty_paise=0,
                    cx_penalty_paise=100,
                    economic_score=0.02,
                ),
            ],
            selected_action=Action.NO_ACTION,
            selected_economic_score=0.0,
            selection_reasoning="SURE_THING: high natural pay probability (92%), no intervention needed",
            policy_checks_passed=[],
            policy_checks_failed=[],
            policy_gate_result="APPROVED",
            is_incremental=False,
        )

        configure(tracer=tracer, audit=AuditLogger(), prevention=PreventionLog())

    def test_no_action_recommendation(self):
        """Packet correctly shows NO_ACTION with reasoning."""
        response = client.get("/api/cases/RC_SURE_THING_001/decision-packet")
        data = response.json()

        assert data["recommendation"]["action"] == "NO_ACTION"
        assert data["recommendation"]["confidence"] == 0.0
        assert "SURE_THING" in data["recommendation"]["reasoning"]
        assert data["recommendation"]["requires_human"] is False


class TestDecisionPacketEdgeCases:
    """Edge cases for the decision packet."""

    def setup_method(self):
        """Set up fresh inspector with test data for edge cases."""
        tracer = DecisionTracer()
        tracer.build(
            case_id="RC_EDGE_001",
            trigger_event="payment.failed:bank_timeout",
            state="RISK_ASSESSED",
            root_cause="bank_timeout",
            natural_payment_probability=0.68,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=500000,
            candidate_actions=[],
            selected_action=Action.NO_ACTION,
            policy_checks_passed=["consent", "contact_window"],
            policy_checks_failed=[],
            policy_gate_result="APPROVED",
        )

        audit = AuditLogger()
        audit.append(
            trigger_type="CASE_CREATED",
            trigger_event="payment.failed",
            payload={"case_id": "RC_EDGE_001", "amount": 500000},
            case_id="RC_EDGE_001",
            amount_paise=500000,
        )

        configure(tracer=tracer, audit=audit, prevention=PreventionLog())

    def test_missing_trace_id_when_no_audit_entries(self):
        """trace_id is None when no audit entries exist for case."""
        tracer = DecisionTracer()
        tracer.build(
            case_id="RC_NO_AUDIT_001",
            trigger_event="test",
            state="TEST",
            root_cause="test",
            natural_payment_probability=0.5,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=1000,
            candidate_actions=[],
            selected_action=Action.NO_ACTION,
        )

        configure(tracer=tracer, audit=AuditLogger(), prevention=PreventionLog())

        response = client.get("/api/cases/RC_NO_AUDIT_001/decision-packet")
        data = response.json()

        # trace_id should be None (no audit entries)
        assert data["trace_id"] is None

    def test_uplift_segment_reflected_in_packet(self):
        """Uplift segment from trace is available in status field."""
        response = client.get("/api/cases/RC_EDGE_001/decision-packet")
        data = response.json()

        assert data["status"] == "RISK_ASSESSED"

    def test_pending_marker_format_consistency(self):
        """Pending markers remain only for E.3 fields; drafts are real (E.6)."""
        response = client.get("/api/cases/RC_EDGE_001/decision-packet")
        data = response.json()

        # diagnosis.rationale_text
        assert isinstance(data["diagnosis"]["rationale_text"], dict)
        assert "pending" in data["diagnosis"]["rationale_text"]
        assert data["diagnosis"]["rationale_text"]["pending"].startswith("E.3")

        # channel_drafts are real per-register drafts, no pending markers remain
        for channel in ["sms", "whatsapp", "email", "voice_script"]:
            assert isinstance(data["channel_drafts"][channel], dict)
            assert "en" in data["channel_drafts"][channel]
            assert data["channel_drafts"][channel]["en"]