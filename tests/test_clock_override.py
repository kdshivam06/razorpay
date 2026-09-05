"""E.12 demo clock — overrideable UTC wall clock across time-sensitive paths."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.audit.decision_trace import DecisionTracer
from app.core.clock import clock
from app.main import app
from app.nlp.ptp_extractor import PtpExtractor

_UTC = timezone.utc


@pytest.fixture(autouse=True)
def _restore_clock():
    try:
        clock.reset()
        yield
    finally:
        clock.reset()


class TestClockPrimitives:
    def test_defaults_to_real_time(self) -> None:
        assert clock.is_overridden() is False
        before = datetime.now(_UTC)
        now = clock.now()
        after = datetime.now(_UTC)
        assert before <= now <= after
        assert clock.today() == now.date()

    def test_set_pins_instant(self) -> None:
        pinned = datetime(2026, 6, 16, 12, 30, tzinfo=_UTC)
        clock.set(pinned)
        assert clock.is_overridden() is True
        assert clock.now() == pinned
        assert clock.today() == date(2026, 6, 16)

    def test_naive_datetime_interpreted_as_utc(self) -> None:
        clock.set(datetime(2026, 6, 16, 12, 30))  # noqa: DTZ001 - naive input is the contract under test
        assert clock.now().tzinfo == _UTC
        assert clock.now().hour == 12

    def test_set_none_restores_real_time(self) -> None:
        clock.set(datetime(2026, 6, 16, tzinfo=_UTC))
        clock.set(None)
        assert clock.is_overridden() is False

    def test_set_rejects_non_datetime(self) -> None:
        with pytest.raises(TypeError):
            clock.set("2026-06-16")

    def test_advance_pinned_is_cumulative(self) -> None:
        clock.set(datetime(2026, 6, 16, 0, 0, tzinfo=_UTC))
        after_one_day = clock.advance(days=1)
        assert after_one_day == datetime(2026, 6, 17, 0, 0, tzinfo=_UTC)
        after_more = clock.advance(hours=6)
        assert after_more == datetime(2026, 6, 17, 6, 0, tzinfo=_UTC)
        assert clock.today() == date(2026, 6, 17)

    def test_advance_unninned_pins_from_real_time(self) -> None:
        before = datetime.now(_UTC)
        pinned = clock.advance(hours=-48)
        after = datetime.now(_UTC)
        assert clock.is_overridden() is True
        assert before - timedelta(hours=49) <= pinned <= after - timedelta(hours=47)

    def test_reset_returns_to_real_time(self) -> None:
        clock.set(datetime(2026, 6, 16, tzinfo=_UTC))
        clock.reset()
        assert clock.is_overridden() is False
        delta = datetime.now(_UTC) - clock.now()
        assert abs(delta.total_seconds()) < 5


class TestClockConsumers:
    def test_decision_trace_timestamp_uses_pin(self) -> None:
        pinned = datetime(2026, 6, 16, 9, 15, tzinfo=_UTC)
        clock.set(pinned)
        tracer = DecisionTracer()
        tracer.build(
            case_id="CLOCK_TRACE",
            trigger_event="overdue.invoice.dunning",
            state="OVERDUE",
            root_cause="overdue_invoice",
            natural_payment_probability=0.2,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=1_000_000,
            candidate_actions=[],
            selected_action=None,
            policy_gate_result="APPROVED",
        )
        assert tracer._traces["CLOCK_TRACE"][0].trigger_timestamp == pinned

    def test_ptp_extractor_default_today_uses_pin(self) -> None:
        clock.set(datetime(2026, 6, 16, 9, 0, tzinfo=_UTC))
        assert PtpExtractor().today == date(2026, 6, 16)

    def test_msmed_calculator_defaults_today_to_clock(self) -> None:
        from app.b2b.msmed_interest import MsmedInterestCalculator

        clock.set(datetime(2026, 6, 16, 0, 0, tzinfo=_UTC))
        run = MsmedInterestCalculator(bank_rate_percent=6.0).run(
            principal_paise=10_000_000, invoice_date=date(2026, 1, 1)
        )
        assert run.elapsed_days == 121
        assert run.accrued_interest_paise == 618_943
        assert run.total_statutory_claim_paise == 10_618_943

    def test_inspector_today_reflects_clock(self) -> None:
        from app.dashboard.case_inspector import CaseInspector

        clock.set(datetime(2026, 6, 16, 0, 0, tzinfo=_UTC))
        assert CaseInspector()._today() == date(2026, 6, 16)


class TestDevClockEndpoints:
    def test_dev_clock_gated_outside_development(self, monkeypatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        client = TestClient(app)
        resp = client.get("/api/dev/clock")
        assert resp.status_code == 404
        assert client.post("/api/dev/advance-clock?hours=24").status_code == 404

    def test_dev_advance_reset_roundtrip(self, monkeypatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "development")
        client = TestClient(app)
        resp = client.post("/api/dev/advance-clock?hours=72")
        assert resp.status_code == 200
        body = resp.json()
        assert body["overridden"] is True
        assert body["today"] == (datetime.now(_UTC) + timedelta(days=3)).date().isoformat()

        status = client.get("/api/dev/clock").json()
        assert status["overridden"] is True
        assert status["today"] == body["today"]

        reset = client.post("/api/dev/reset-clock").json()
        assert reset["overridden"] is False

    def test_dev_advance_rejects_absurd_hours(self, monkeypatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "development")
        client = TestClient(app)
        assert (
            client.post("/api/dev/advance-clock?hours=100000").status_code == 400
        )


class TestMsmedLadderEndToEndDrivenByClock:
    """Pin the clock to 2026-06-16 and step it to watch §16 interest accrue."""

    def test_status_and_refresh_after_clock_step(self, monkeypatch) -> None:
        from app.audit.audit_logger import AuditLogger
        from app.audit.decision_trace import DecisionTracer
        from app.audit.prevention_log import PreventionLog
        from app.b2b.msmed_interest import MsmedInterestCalculator
        from app.b2b.msmed_ladder import (
            B2BReceivable,
            MsmedFilingRegistry,
        )
        from app.b2b.statutory_notice import StatutoryNoticeGenerator
        from app.dashboard.case_inspector import configure

        monkeypatch.setenv("ENVIRONMENT", "development")
        case_id = "CLOCK_MSMED"
        tracer = DecisionTracer()
        audit = AuditLogger()
        tracer.build(
            case_id=case_id,
            trigger_event="overdue.invoice.dunning",
            state="OVERDUE",
            root_cause="overdue_invoice",
            natural_payment_probability=0.15,
            uplift_segment="CONCESSION_ELIGIBLE",
            revenue_at_risk_paise=10_000_000,
            candidate_actions=[],
            selected_action=None,
            policy_gate_result="APPROVED",
        )
        configure(
            tracer=tracer,
            audit=audit,
            prevention=PreventionLog(),
            outbox=[],
            msmed_calculator=MsmedInterestCalculator(bank_rate_percent=6.0),
            msmed_notices=StatutoryNoticeGenerator(),
            msmed_filings=MsmedFilingRegistry(),
            receivable_profiles={
                case_id: B2BReceivable(
                    case_id=case_id,
                    invoice_id="INV-CLOCK",
                    buyer_name="Clock Works Pvt Ltd",
                    buyer_category="private",
                    invoice_date=date(2026, 1, 1),
                    amount_paise=10_000_000,
                    supplier_msme_registered=True,
                    profile_source="stored",
                )
            },
        )

        clock.set(datetime(2026, 6, 16, 0, 0, tzinfo=_UTC))
        client = TestClient(app)
        status = client.get(f"/api/cases/{case_id}/msmed/status")
        assert status.status_code == 200
        body = status.json()
        assert body["days_since_invoice"] == 167
        assert body["rung_number"] == 4
        assert body["interest_paise"] == 618_943
        assert body["notice"]["en"]["source"] == "fallback"

        clock.advance(days=1)
        refreshed = client.post(f"/api/cases/{case_id}/msmed/status").json()
        assert refreshed["days_since_invoice"] == 168
        assert refreshed["interest_paise"] > 618_943

        clock.reset()
        real_status = client.get(f"/api/cases/{case_id}/msmed/status").json()
        assert real_status["days_since_invoice"] != 167

        clock.set(datetime(2026, 6, 16, 0, 0, tzinfo=_UTC))
        for action in ("request", "approve", "dispatch"):
            resp = client.post(
                f"/api/cases/{case_id}/msmed/conciliation",
                json={"action": action},
            )
            assert resp.status_code == 200, (action, resp.text)

        msmed_events = [
            e for e in audit._chain
            if e.trigger_event and e.trigger_event.startswith("msmed_")
        ]
        filing_request = next(
            e for e in msmed_events
            if e.trigger_event == "msmed_conciliation_request"
        )
        assert filing_request.details["legal_basis"].startswith("Micro, Small and Medium Enterprises")
        assert "§18" in filing_request.details["legal_basis"]
        assert filing_request.action == "MSMED_CONCILIATION_REQUESTED"