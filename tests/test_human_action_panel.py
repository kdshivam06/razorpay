"""Test suite: Human action panel (E.7) — POST /api/cases/{id}/action.

Verifies four behaviours required by Track E.7:
  (a) a human approves the AI-selected channel draft → SUCCESS + a real
      NotificationRecord in the shared outbox, plus an audit entry recording
      who acted and why;
  (b) a human overrides the AI suggestion to a *different* channel → SUCCESS
      on that channel;
  (c) a human writes the case off → terminal WRITTEN_OFF obligation + audit;
  (d) a policy gate blocks a human override (e.g. outside contact hours, or a
      revoked consent) → the action returns BLOCKED / HTTP 409 and NOTHING is
      executed — a human can never bypass the safety layer (§7.1).

The no-consent path FAILS CLOSED, so the success-path tests inject a
PolicyEngine with consent pre-granted for the reconstructed customer.
"""

from datetime import datetime, time, timezone

from fastapi.testclient import TestClient

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.contracts import Action, CandidateAction
from app.dashboard.case_inspector import CaseInspector, case_customer_id
from app.executor.human_queue import HumanTaskQueue
from app.main import app
from app.policy.contact_policy import Channel, ChannelWindow, ContactPolicyEngine
from app.policy.policy_engine import PolicyEngine

client = TestClient(app)


def _build_trace(tracer: DecisionTracer, case_id: str, selected: Action = Action.SEND_SMS) -> None:
    """Seed a single decision trace for the case under test."""
    tracer.build(
        case_id=case_id,
        trigger_event="payment.failed:insufficient_funds",
        state="RISK_ASSESSED",
        root_cause="insufficient_funds",
        natural_payment_probability=0.28,
        uplift_segment="PERSUADABLE",
        revenue_at_risk_paise=1_500_000,  # ₹15,000
        candidate_actions=[
            CandidateAction(
                action=selected,
                expected_recovery_paise=900_000,
                communication_cost_paise=2500,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=100,
                economic_score=0.4,
            )
        ],
        selected_action=selected,
        selected_economic_score=0.4,
        selection_reasoning="SMS is the highest positive incremental channel",
        policy_gate_result="APPROVED",
    )


def _consented_policy(customer_id: str, channels=("sms", "whatsapp", "voice", "email")) -> PolicyEngine:
    """A PolicyEngine with explicit TRAI consent granted for *customer_id*."""
    policy = PolicyEngine()
    for channel in channels:
        policy._consent.record_consent(
            customer_id, channel, "payment_recovery", datetime.now(timezone.utc)
        )
    return policy


class TestHumanApprovesAISuggestion:
    """(a) Human approves the AI-selected channel through the API layer."""

    def setup_method(self):
        self.case_id = "RC_PANEL_APPROVE_001"
        self.customer = case_customer_id(self.case_id)
        self.tracer = DecisionTracer()
        _build_trace(self.tracer, self.case_id, Action.SEND_SMS)
        self.outbox: list[object] = []
        policy = _consented_policy(self.customer)
        self.inspector = CaseInspector(
            tracer=self.tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=self.outbox,
            human_queue=HumanTaskQueue(),
            policy_engine=policy,
        )
        from app.dashboard.case_inspector import configure

        configure(
            tracer=self.tracer,
            audit=self.inspector._audit,
            prevention=self.inspector._prevention,
            outbox=self.outbox,
            human_queue=self.inspector._human_queue,
            policy_engine=policy,
        )

    def test_approve_sends_and_records_outbox_and_audit(self):
        """Approving SEND_SMS pushes a real NotificationRecord and an audit entry."""
        resp = client.post(
            f"/api/cases/{self.case_id}/action",
            json={
                "action": "SEND_SMS",
                "channel": "sms",
                "actor": "ops.shivam",
                "reason": "Approving the AI-drafted SMS reminder",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["executed"] is True
        assert body["state"] == "SUCCESS"
        assert body["channel"] == "sms"
        assert body["external_ref"].startswith("notif_")

        assert len(self.outbox) == 1
        record = self.outbox[0]
        assert record.channel == "sms"

        entries = self.inspector._audit.entries_for_case(self.case_id)
        action_entries = [e for e in entries if e.trigger_type == "ACTION_EXECUTED"]
        assert len(action_entries) == 1
        details = action_entries[0].details
        assert details["actor"] == "ops.shivam"
        assert details["reason"] == "Approving the AI-drafted SMS reminder"
        assert details["policy_gate_result"] == "APPROVED"


class TestHumanOverridesChannel:
    """(b) A human may override the AI suggestion to a different channel."""

    def setup_method(self):
        self.case_id = "RC_PANEL_OVERRIDE_001"
        self.customer = case_customer_id(self.case_id)
        self.tracer = DecisionTracer()
        _build_trace(self.tracer, self.case_id, Action.SEND_SMS)  # AI suggested SMS
        self.outbox: list[object] = []
        policy = _consented_policy(self.customer)
        self.inspector = CaseInspector(
            tracer=self.tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=self.outbox,
            human_queue=HumanTaskQueue(),
            policy_engine=policy,
        )
        from app.dashboard.case_inspector import configure

        configure(
            tracer=self.tracer,
            audit=self.inspector._audit,
            prevention=self.inspector._prevention,
            outbox=self.outbox,
            human_queue=self.inspector._human_queue,
            policy_engine=policy,
        )

    def test_override_to_email_succeeds_on_email_channel(self):
        """Human picks Email even though AI proposed SMS → SUCCESS on email."""
        resp = client.post(
            f"/api/cases/{self.case_id}/action",
            json={
                "action": "SEND_EMAIL",
                "channel": "email",
                "actor": "ops.kiran",
                "reason": "Customer prefers email for large balances",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["executed"] is True
        assert body["channel"] == "email"
        assert body["external_ref"].startswith("notif_")

        assert len(self.outbox) == 1
        assert self.outbox[0].channel == "email"


class TestHumanWritesOff:
    """(c) A human writes the case off → terminal WRITTEN_OFF obligation."""

    def setup_method(self):
        self.case_id = "RC_PANEL_WRITEOFF_001"
        self.customer = case_customer_id(self.case_id)
        self.tracer = DecisionTracer()
        _build_trace(self.tracer, self.case_id, Action.SEND_SMS)
        self.outbox: list[object] = []
        self.inspector = CaseInspector(
            tracer=self.tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=self.outbox,
            human_queue=HumanTaskQueue(),
            policy_engine=_consented_policy(self.customer),
        )

    def test_write_off_marks_obligation_terminal_and_records_audit(self):
        """WRITE_OFF transitions the reconstructed obligation to WRITTEN_OFF."""
        outcome = self.inspector.action(
            self.case_id,
            Action.WRITE_OFF,
            actor="ops.ruma",
            reason="Commercially uneconomic to recover",
        )
        assert outcome.executed is True
        assert outcome.state == "SUCCESS"
        assert "Case written off" in outcome.detail

        entries = self.inspector._audit.entries_for_case(self.case_id)
        action_entries = [e for e in entries if e.trigger_type == "ACTION_EXECUTED"]
        assert len(action_entries) == 1
        assert "WRITE_OFF" in action_entries[0].trigger_event
        assert action_entries[0].details["actor"] == "ops.ruma"
        assert action_entries[0].details["reason"] == "Commercially uneconomic to recover"


class TestPolicyGateBlocksHumanOverride:
    """(d) A human override NEVER bypasses the policy safety layer (§7.1).

    Two deterministic blocks are exercised:
      - outside the configured contact window (voice call at a barred time);
      - revoked consent through the HTTP endpoint (returns 409).
    """

    def test_contact_window_blocks_voice_at_unit_level(self):
        """A voice call outside contact hours is blocked with hold_until."""
        case_id = "RC_PANEL_WINDOW_001"
        customer = case_customer_id(case_id)
        tracer = DecisionTracer()
        _build_trace(tracer, case_id, Action.VOICE_CALL)

        # Voice is only allowed 00:00–00:01 (Asia/Kolkata); act at a time outside it.
        at = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)  # 15:30 IST — blocked
        contact = ContactPolicyEngine(
            windows={
                Channel.VOICE: ChannelWindow(
                    channel=Channel.VOICE,
                    start=time(0, 0),
                    end=time(0, 1),
                    timezone_name="Asia/Kolkata",
                )
            }
        )
        policy = PolicyEngine(contact=contact)
        policy._consent.record_consent(
            customer, "voice", "payment_recovery", datetime.now(timezone.utc)
        )

        inspector = CaseInspector(
            tracer=tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            policy_engine=policy,
        )
        outcome = inspector.action(
            case_id,
            Action.VOICE_CALL,
            channel="voice",
            actor="ops.forceful",
            reason="Trying to call outside hours regardless",
            at=at,
        )
        assert outcome.executed is False
        assert outcome.state == "BLOCKED"
        assert outcome.policy_blocked_reasons
        assert any("contact window" in r for r in outcome.policy_blocked_reasons)
        assert outcome.hold_until is not None

        # Nothing was executed and a BLOCKED audit entry was recorded.
        entries = inspector._audit.entries_for_case(case_id)
        assert any(e.trigger_type == "ACTION_BLOCKED" for e in entries)

    def test_revoked_consent_blocks_endpoint_with_409(self):
        """The HTTP endpoint returns 409 and executes nothing when consent is revoked."""
        case_id = "RC_PANEL_REVOKE_001"
        customer = case_customer_id(case_id)
        tracer = DecisionTracer()
        _build_trace(tracer, case_id, Action.SEND_SMS)

        policy = _consented_policy(customer)
        # Revoke consent so a later SEND_* is blocked (fails closed).
        policy._consent.revoke_consent(
            customer, "sms", "payment_recovery", datetime.now(timezone.utc)
        )

        outbox: list[object] = []
        inspector = CaseInspector(
            tracer=tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=outbox,
            human_queue=HumanTaskQueue(),
            policy_engine=policy,
        )
        from app.dashboard.case_inspector import configure

        configure(
            tracer=tracer,
            audit=inspector._audit,
            prevention=inspector._prevention,
            outbox=outbox,
            human_queue=inspector._human_queue,
            policy_engine=policy,
        )

        resp = client.post(
            f"/api/cases/{case_id}/action",
            json={
                "action": "SEND_SMS",
                "channel": "sms",
                "actor": "ops.forceful",
                "reason": "Approve despite no consent",
            },
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["detail"]["state"] == "BLOCKED"
        assert any("consent" in r for r in data["detail"]["policy_blocked_reasons"])
        assert len(outbox) == 0  # nothing was sent

        entries = inspector._audit.entries_for_case(case_id)
        assert any(e.trigger_type == "ACTION_BLOCKED" for e in entries)

    def test_unknown_action_returns_400(self):
        """An invalid action string is rejected before any execution."""
        case_id = "RC_PANEL_400_001"
        tracer = DecisionTracer()
        _build_trace(tracer, case_id)
        inspector = CaseInspector(
            tracer=tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            policy_engine=_consented_policy(case_customer_id(case_id)),
        )
        from app.dashboard.case_inspector import configure

        configure(
            tracer=tracer,
            audit=inspector._audit,
            prevention=inspector._prevention,
            policy_engine=inspector._policy,
        )
        resp = client.post(
            f"/api/cases/{case_id}/action",
            json={"action": "DROP_TABLE", "actor": "ops.shivam", "reason": "x"},
        )
        assert resp.status_code == 400
