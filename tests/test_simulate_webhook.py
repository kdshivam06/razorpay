"""Test suite: Razorpay Payment Links + dev-only simulated webhooks (E.9).

POST /api/cases/{case_id}/payment-link must:
  (a) create a real Razorpay Test Mode Payment Link through the gateway
      (injected here as a fake so nothing touches the network) and return the
      Razorpay-hosted test URL + rzp.io short URL + audit entry;
  (b) reuse an existing active link per §8.4 instead of spamming the API;
  (c) surface gateway failures cleanly — 502 on API/TIMEOUT errors, which are
      UNKNOWN (§3.5) and must route to reconciliation, not be retried.

POST /api/dev/simulate-webhook/payment-link-paid must:
  (d) be gated on ENVIRONMENT=development / APP_ENV=development — 404 otherwise;
  (e) build a Razorpay-shaped `payment_link.paid` event, HMAC-SHA256 sign it
      with the shared WEBHOOK_SECRET (same scheme webhook_validator expects),
      and POST it to the app's OWN /webhooks gateway so it passes verify →
      freshness → dedup → EventInbox like a real webhook;
  (f) on ACCEPTED mark the link PAID and write a reconciled audit entry,
      closing the create → paid → reconcile loop.
"""

import time
import uuid

from fastapi.testclient import TestClient

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.config import get_settings
from app.contracts import Action, CandidateAction, PaymentLinkState
from app.dashboard.case_inspector import _is_development, configure
from app.executor.payment_link import PaymentLinkGatewayError, PaymentLinkLifecycle
from app.ingestion.webhook_handler import WebhookHandler, WebhookResultStatus
from app.ingestion.webhook_simulator import build_payment_link_paid_event, encode_event
from app.ingestion.webhook_validator import validate_event, verify_signature
from app.main import app

client = TestClient(app)

SECRET = get_settings().WEBHOOK_SECRET


class FakeRazorpayGateway:
    """Fake Razorpay Payment Links gateway — no network, deterministic."""

    def __init__(self, *, failure: Exception | None = None):
        self._failure = failure
        self.calls: list[dict] = []
        self.created: list[dict] = []

    @property
    def available(self) -> bool:
        return True

    def create_link(
        self,
        *,
        case_id: str,
        amount_paise: int,
        description: str,
        notes: dict[str, str] | None = None,
        customer: dict[str, str] | None = None,
        reference_id: str | None = None,
        expire_by: int | None = None,
    ) -> dict:
        self.calls.append(
            {
                "case_id": case_id,
                "amount_paise": amount_paise,
                "description": description,
                "notes": notes,
                "reference_id": reference_id,
            }
        )
        if self._failure is not None:
            raise self._failure
        link_id = "plink_REAL" + uuid.uuid4().hex[:10]
        resp = {
            "id": link_id,
            "short_url": f"https://rzp.io/i/{link_id[-8:]}",
            "status": "created",
            "amount": amount_paise,
            "currency": "INR",
            "created_at": int(time.time()),
        }
        self.created.append(resp)
        return resp


def _build_trace(tracer: DecisionTracer, case_id: str) -> None:
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
                action=Action.SEND_PAYMENT_LINK,
                expected_recovery_paise=900_000,
                communication_cost_paise=2500,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=100,
                economic_score=0.4,
            )
        ],
        selected_action=Action.SEND_PAYMENT_LINK,
        selected_economic_score=0.4,
        selection_reasoning="Payment link is the highest positive incremental channel",
        policy_gate_result="APPROVED",
    )


class TestPaymentLinkLifecycle:
    """§8.4 lifecycle + real-URL capture through the gateway."""

    def test_create_via_gateway_stores_real_urls(self):
        gateway = FakeRazorpayGateway()
        lifecycle = PaymentLinkLifecycle()
        record = lifecycle.create_or_reuse(
            "RC_PL_001", 1_500_000, gateway=gateway
        )

        assert len(gateway.calls) == 1
        assert gateway.calls[0]["case_id"] == "RC_PL_001"
        assert gateway.calls[0]["amount_paise"] == 1_500_000
        assert record.razorpay_link_id.startswith("plink_REAL")
        assert record.link_id == record.razorpay_link_id
        assert record.short_url.startswith("https://rzp.io/i/")
        assert record.payment_url.startswith(
            f"https://razorpay.com/payment-link/{record.razorpay_link_id}"
        )
        assert record.state is PaymentLinkState.CREATED

    def test_reuses_active_link_instead_of_new_api_call(self):
        gateway = FakeRazorpayGateway()
        lifecycle = PaymentLinkLifecycle()
        first = lifecycle.create_or_reuse("RC_PL_001", 1_500_000, gateway=gateway)
        second = lifecycle.create_or_reuse("RC_PL_001", 500_000, gateway=gateway)

        assert first.link_id == second.link_id
        assert len(gateway.calls) == 1, "existing active link must be reused"

    def test_mark_paid_closes_link(self):
        gateway = FakeRazorpayGateway()
        lifecycle = PaymentLinkLifecycle()
        record = lifecycle.create_or_reuse("RC_PL_001", 1_500_000, gateway=gateway)
        paid = lifecycle.mark_paid("RC_PL_001", record.link_id, amount_paid_paise=1_500_000)

        assert paid.state is PaymentLinkState.PAID
        assert paid.amount_paid_paise == 1_500_000
        assert paid.amount_outstanding_paise == 0
        assert lifecycle.latest("RC_PL_001").state is PaymentLinkState.PAID

    def test_gateway_timeout_propagates_as_unknown(self):
        gateway = FakeRazorpayGateway(
            failure=PaymentLinkGatewayError("TIMEOUT", "ReadTimeout: timed out")
        )
        lifecycle = PaymentLinkLifecycle()
        try:
            lifecycle.create_or_reuse("RC_PL_001", 1_500_000, gateway=gateway)
        except PaymentLinkGatewayError as exc:
            assert exc.kind == "TIMEOUT"
        else:
            raise AssertionError("gateway error must propagate (UNKNOWN, §3.5)")


class TestWebhookSimulator:
    """Payload builder + signer produce events the validator accepts."""

    def test_event_shape_and_signature_roundtrip(self):
        event = build_payment_link_paid_event(
            link_id="plink_REALabc",
            case_id="RC_PL_001",
            amount_paise=1_500_000,
            short_url="https://rzp.io/i/payme",
        )
        assert event["entity"] == "event"
        assert event["event"] == "payment_link.paid"
        assert event["id"].startswith("evt_sim_plpaid_")
        assert isinstance(event["created_at"], int)
        pl = event["payload"]["payment_link"]["entity"]
        assert pl["id"] == "plink_REALabc"
        assert pl["status"] == "paid"
        assert pl["notes"]["case_id"] == "RC_PL_001"
        pay = event["payload"]["payment"]["entity"]
        assert pay["status"] == "captured"
        assert pay["id"].startswith("pay_sim_")

        body, signature_header = encode_event(event, SECRET)
        assert signature_header.startswith("sha256=")
        verified = validate_event(body, signature_header, [SECRET])
        assert verified.event_id == event["id"]
        assert verified.payload["event"] == "payment_link.paid"

    def test_tampered_event_has_different_signature(self):
        event = build_payment_link_paid_event(
            link_id="plink_REALabc",
            case_id="RC_PL_001",
            amount_paise=1_500_000,
        )
        body, signature_header = encode_event(event, SECRET)
        tampered = body.replace(b"plink_REALabc", b"plink_TAMPERED")
        assert tampered != body
        assert verify_signature(tampered, signature_header, SECRET) is False

    def test_live_handler_accepts_simulated_webhook(self):
        handler = WebhookHandler(secrets=[SECRET])
        event = build_payment_link_paid_event(
            link_id="plink_REALabc",
            case_id="RC_PL_001",
            amount_paise=1_500_000,
        )
        body, signature_header = encode_event(event, SECRET)
        result = handler.handle(body, {"x-razorpay-signature": signature_header})
        assert result.status is WebhookResultStatus.ACCEPTED
        assert result.event_id == event["id"]

        again = handler.handle(body, {"x-razorpay-signature": signature_header})
        assert again.status is WebhookResultStatus.DUPLICATE


class TestPaymentLinkApi:
    """POST /api/cases/{case_id}/payment-link — real gateway wiring + audit."""

    def setup_method(self):
        self.case_id = "RC_PAYLINK_API_001"
        self.tracer = DecisionTracer()
        _build_trace(self.tracer, self.case_id)
        self.audit = AuditLogger()
        self.gateway = FakeRazorpayGateway()
        configure(
            tracer=self.tracer,
            audit=self.audit,
            prevention=PreventionLog(),
            outbox=[],
            razorpay_client=self.gateway,
        )

    def test_generate_returns_real_test_url_and_audits(self):
        resp = client.post(
            f"/api/cases/{self.case_id}/payment-link",
            json={"actor": "ops.shivam"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "real"
        assert body["link_id"].startswith("plink_REAL")
        assert body["payment_url"].startswith(
            f"https://razorpay.com/payment-link/{body['link_id']}"
        )
        assert body["short_url"].startswith("https://rzp.io/i/")
        assert body["state"] == "CREATED"
        assert body["amount_paise"] == 1_500_000

        gens = [
            e for e in self.audit.entries_for_case(self.case_id)
            if e.trigger_type == "PAYMENT_LINK_GENERATED"
        ]
        assert len(gens) == 1
        assert gens[0].details["link_id"] == body["link_id"]
        assert gens[0].details["source"] == "real"
        assert self.audit.verify_chain() == []

    def test_reuses_previously_generated_link(self):
        client.post(f"/api/cases/{self.case_id}/payment-link", json={})
        resp = client.post(f"/api/cases/{self.case_id}/payment-link", json={})
        assert resp.status_code == 200
        assert len(self.gateway.calls) == 1, "same active link must be reused"

    def test_unknown_case_returns_404(self):
        resp = client.post("/api/cases/RC_NO_SUCH/payment-link", json={})
        assert resp.status_code == 404
        assert "No decision packet" in resp.json()["detail"]

    def test_gateway_api_error_returns_502(self):
        configure(
            tracer=self.tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=[],
            razorpay_client=FakeRazorpayGateway(
                failure=PaymentLinkGatewayError("API_ERROR", "razorpay: code 400")
            ),
        )
        resp = client.post(f"/api/cases/{self.case_id}/payment-link", json={})
        assert resp.status_code == 502
        assert "API_ERROR" in resp.json()["detail"]


class TestSimulatePaymentLinkPaid:
    """POST /api/dev/simulate-webhook/payment-link-paid — dev-gated E.9 demo."""

    def setup_method(self):
        self.case_id = "RC_SIM_WEBHOOK_001"
        self.tracer = DecisionTracer()
        _build_trace(self.tracer, self.case_id)
        self.audit = AuditLogger()
        self.gateway = FakeRazorpayGateway()
        configure(
            tracer=self.tracer,
            audit=self.audit,
            prevention=PreventionLog(),
            outbox=[],
            razorpay_client=self.gateway,
        )
        client.post(f"/api/cases/{self.case_id}/payment-link", json={})

    def _simulate(self, **extra):
        return client.post(
            "/api/dev/simulate-webhook/payment-link-paid",
            json={"case_id": self.case_id, "actor": "dev.tester", **extra},
        )

    def test_webhook_is_signed_accepted_and_link_paid(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        resp = self._simulate()
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["link_state"] == "PAID"
        assert body["payment_link_id"].startswith("plink_REAL")
        assert body["event_id"].startswith("evt_sim_plpaid_")
        assert body["payment_id"].startswith("pay_sim_")
        assert body["webhook_status_code"] == 200
        assert "PAID" in body["detail"]

        paid = [
            e for e in self.audit.entries_for_case(self.case_id)
            if e.trigger_type == "PAYMENT_LINK_PAID"
        ]
        assert len(paid) == 1
        assert paid[0].details["reconciled"] is True
        assert paid[0].details["payment_id"] == body["payment_id"]
        assert self.audit.verify_chain() == []

    def test_second_simulation_conflicts_409(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert self._simulate().status_code == 200
        again = self._simulate()
        assert again.status_code == 409
        assert "already PAID" in again.json()["detail"]

    def test_no_generated_link_returns_404(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        configure(
            tracer=DecisionTracer(),
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=[],
            razorpay_client=FakeRazorpayGateway(),
        )
        resp = self._simulate()
        assert resp.status_code == 404
        assert "No payment link generated" in resp.json()["detail"]

    def test_blocked_outside_development(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        resp = self._simulate()
        assert resp.status_code == 404
        assert "Dev-only" in resp.json()["detail"]


class TestDevelopmentGate:
    """ENVIRONMENT/APP_ENV gating for the dev simulate endpoint (E.9)."""

    def test_env_variable_wins(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert _is_development() is False
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert _is_development() is True

    def test_falls_back_to_app_env(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        import app.dashboard.case_inspector as inspector_mod

        monkeypatch.setattr(inspector_mod, "get_settings", lambda: type(
            "FakeSettings", (), {"APP_ENV": "development"}
        )())
        assert _is_development() is True

        monkeypatch.setattr(inspector_mod, "get_settings", lambda: type(
            "FakeSettings", (), {"APP_ENV": "production"}
        )())
        assert _is_development() is False