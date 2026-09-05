"""Test suite: MSMED Act 2006 §16 interest calculator + ladder (E.10).

   1. §16 interest math is LIVE and hand-auditable — the hand-computed example
      from the implementation notes must match exactly (30-day monthly rests,
      accrual from the day after the 45-day window, rounding to the paise).
   2. The 4-rung ladder is an explicit state machine with a hard human
      sign-off gate on Rung 4 — no code path auto-files a legal notice.
   3. Buyers of non-MSME suppliers get "standard commercial terms apply —
      §16 does not apply" and zero statutory interest.
   4. Statutory notices reuse the E.6 guardrails: LLM drafts are validated
      against the case record and hallucinated figures force a deterministic
      fallback.
   5. The inspector routes surface the ladder + filing workflow with the same
      TestClient wiring used by the E.9 payment-link tests.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.b2b.msmed_interest import (
    MsmedInterestCalculator,
    accrual_elapsed_days,
    accrual_start_date,
    compute_interest_run,
    monthly_rate_percent,
    statutory_rate_percent,
)
from app.b2b.msmed_ladder import (
    B2BReceivable,
    FilingState,
    MsmedEscalationLadder,
    MsmedFilingRegistry,
    rung_for,
)
from app.b2b.statutory_notice import (
    FORMAT_INR,
    StatutoryNoticeGenerator,
    build_notice_context,
    build_notice_spec,
)
from app.contracts import Action, CandidateAction
from app.dashboard.case_inspector import configure
from app.main import app
from app.nlp.message_templates import validate_draft

client = TestClient(app)

# Hand-computed reference example (implementation notes, E.10):
PRINCIPAL_PAISE = 10_000_000  # ₹100,000
INVOICE_DATE = date(2026, 1, 1)
RATE = 6.0  # RBI bank rate % p.a.
HAND_INTEREST_PAISE = 618_943
HAND_CLAIM_PAISE = 10_618_943


def _make_tracer(
    case_id: str,
    *,
    tracer: DecisionTracer | None = None,
    root_cause: str = "overdue_invoice",
) -> DecisionTracer:
    tracer = tracer or DecisionTracer()
    tracer.build(
        case_id=case_id,
        trigger_event="overdue.invoice.dunning",
        state="OVERDUE",
        root_cause=root_cause,
        natural_payment_probability=0.15,
        uplift_segment="CONCESSION_ELIGIBLE",
        revenue_at_risk_paise=PRINCIPAL_PAISE,
        candidate_actions=[
            CandidateAction(
                action=Action.SEND_SMS,
                expected_recovery_paise=PRINCIPAL_PAISE,
                communication_cost_paise=500,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=0,
                economic_score=0.55,
            )
        ],
        selected_action=Action.SEND_SMS,
        selected_economic_score=0.55,
        selection_reasoning="Statutory dunning path for overdue B2B invoice",
        policy_gate_result="APPROVED",
    )
    return tracer


def _make_receivable(
    case_id: str,
    *,
    invoice_date: date = INVOICE_DATE,
    registered: bool = True,
) -> B2BReceivable:
    return B2BReceivable(
        case_id=case_id,
        invoice_id="INV-B2B",
        buyer_name="Acme Pvt Ltd",
        buyer_category="private",
        invoice_date=invoice_date,
        amount_paise=PRINCIPAL_PAISE,
        supplier_msme_registered=registered,
        profile_source="stored",
    )


# ── 1. §16 interest math ─────────────────────────────────────────────────


class TestStatutoryInterestRate:
    def test_statutory_rate_is_three_times_bank_rate(self):
        assert statutory_rate_percent(6.0) == 18.0
        assert statutory_rate_percent(6.25) == 18.75
        assert monthly_rate_percent(6.0) == 1.5

    def test_default_bank_rate(self):
        assert monthly_rate_percent(None) == 18.75 / 12
        assert statutory_rate_percent(None) == 18.75


class TestHandComputedInterestRun:
    def test_full_run_matches_hand_computation(self):
        run = compute_interest_run(PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 6, 16), RATE)

        assert run.applies is True
        assert run.bank_rate_percent == 6.0
        assert run.statutory_rate_percent == 18.0
        assert run.monthly_rate_percent == 1.5
        assert run.accrual_start_date == date(2026, 2, 16)  # day after 45-day window
        assert run.elapsed_days == 121  # inclusive: 2026-02-16 .. 2026-06-16
        assert run.months_compounded == 4
        assert run.partial_days == 1
        assert run.accrued_interest_paise == HAND_INTEREST_PAISE
        assert run.total_statutory_claim_paise == HAND_CLAIM_PAISE

    def test_first_accrual_day_is_5000_paise(self):
        run = compute_interest_run(PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 2, 16), RATE)
        assert run.elapsed_days == 1
        assert run.accrued_interest_paise == 5_000  # 1% of 1e6 paise on 1e4 rupees
        assert run.total_statutory_claim_paise == 10_005_000

    def test_no_interest_before_day_45(self):
        run = compute_interest_run(PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 2, 10), RATE)
        assert run.applies is False
        assert run.elapsed_days == 0
        assert run.accrued_interest_paise == 0
        assert run.total_statutory_claim_paise == PRINCIPAL_PAISE

    def test_day_45_itself_is_still_free(self):
        run = compute_interest_run(PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 2, 15), RATE)
        assert run.elapsed_days == 0
        assert run.accrued_interest_paise == 0

    def test_helper_functions_agree(self):
        assert accrual_start_date(INVOICE_DATE) == date(2026, 2, 16)
        assert accrual_elapsed_days(INVOICE_DATE, date(2026, 2, 16)) == 1
        assert accrual_elapsed_days(INVOICE_DATE, date(2026, 2, 15)) == 0

    def test_interest_grows_over_time(self):
        day_52 = compute_interest_run(PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 2, 23), RATE)
        day_60 = compute_interest_run(PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 3, 3), RATE)
        assert day_60.accrued_interest_paise > day_52.accrued_interest_paise

    def test_live_calculator_recomputes_from_today(self):
        calc = MsmedInterestCalculator(bank_rate_percent=RATE)
        assert calc.accrued_interest_to_date(
            PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 6, 16)
        ) == HAND_INTEREST_PAISE
        assert calc.total_statutory_claim(
            PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 6, 16)
        ) == HAND_CLAIM_PAISE


# ── 2. Ladder state machine ──────────────────────────────────────────────


class TestLadderRungs:
    def test_rung_mapping_boundaries(self):
        assert rung_for(1).number == 1
        assert rung_for(15).number == 1
        assert rung_for(16).number == 2
        assert rung_for(30).number == 2
        assert rung_for(31).number == 3
        assert rung_for(44).number == 3
        assert rung_for(45).number == 4
        assert rung_for(200).number == 4

    def test_rung_labels_and_notice_flag(self):
        ladder = MsmedEscalationLadder()
        assert ladder.day_range(ladder.rung(16)) == (16, 30)
        assert ladder.day_range(ladder.rung(45))[0] == 45
        assert ladder.rung(16).label == "§16 interest advisory"
        assert ladder.rung(16).has_statutory_notice is True
        assert ladder.rung(1).has_statutory_notice is False
        assert ladder.rung(31).has_statutory_notice is True
        assert ladder.rung(45).has_statutory_notice is True

    def test_advance_reports_escalation_crossing_days_15_16(self):
        ladder = MsmedEscalationLadder()
        receivable = _make_receivable("RC_ESC")
        transition = ladder.advance(receivable, date(2026, 1, 16))
        assert transition.from_rung.number == 1
        assert transition.to_rung.number == 2
        assert transition.escalated is True

    def test_advance_does_not_escalate_within_rung(self):
        ladder = MsmedEscalationLadder()
        receivable = _make_receivable("RC_SAME")
        transition = ladder.advance(receivable, date(2026, 1, 15))
        assert transition.from_rung.number == 1
        assert transition.to_rung.number == 1
        assert transition.escalated is False


class TestRung4ConciliationFile:
    def test_request_only_applies_from_day_45(self):
        ladder = MsmedEscalationLadder()
        young = _make_receivable("RC_YOUNG")
        with pytest.raises(ValueError, match="Day 45"):
            # Day 44 → still inside the §15 acceptance window
            ladder.request_conciliation_filing(young, date(2026, 2, 13))
        old = _make_receivable("RC_OLD")
        # Day 46 (diff 45 + 1) → comfortably inside the eligibility window
        filing = ladder.request_conciliation_filing(old, date(2026, 2, 15))
        assert filing.state is FilingState.PENDING_SIGNOFF
        assert filing.filing_reference.startswith("SAMADHAAN-")

    def test_approval_requires_pending_signoff(self):
        ladder = MsmedEscalationLadder()
        old = _make_receivable("RC_OLD2")
        filing = ladder.request_conciliation_filing(old, date(2026, 2, 16))
        approved = ladder.approve_conciliation_filing(
            filing, approved_by="ops.rama", today=date(2026, 2, 17)
        )
        assert approved.state is FilingState.APPROVED
        assert approved.approved_by == "ops.rama"
        with pytest.raises(ValueError, match="not PENDING_SIGNOFF"):
            ladder.approve_conciliation_filing(approved, approved_by="ops.rama")

    def test_dispatch_requires_approved_signoff_only(self):
        ladder = MsmedEscalationLadder()
        old = _make_receivable("RC_OLD3")
        pending = ladder.request_conciliation_filing(old, date(2026, 2, 16))
        # Hard rule: cannot dispatch a filing that has not been approved.
        with pytest.raises(ValueError, match="human sign-off"):
            ladder.dispatch_conciliation_filing(pending, today=date(2026, 2, 16))
        approved = ladder.approve_conciliation_filing(
            pending, approved_by="ops.rama", today=date(2026, 2, 17)
        )
        dispatched = ladder.dispatch_conciliation_filing(approved, today=date(2026, 2, 18))
        assert dispatched.state is FilingState.FILED
        assert dispatched.dispatched is True

    def test_registry_order_approve_then_dispatch(self):
        registry = MsmedFilingRegistry()
        ladder = MsmedEscalationLadder()
        old = _make_receivable("RC_REG")
        filing = ladder.request_conciliation_filing(old, date(2026, 2, 16))
        registry.record(filing)
        assert registry.get("RC_REG").state is FilingState.PENDING_SIGNOFF

        approved = registry.approve("RC_REG", approved_by="ops.rama", today=date(2026, 2, 17))
        assert approved.state is FilingState.APPROVED
        with pytest.raises(ValueError, match="only PENDING_SIGNOFF"):
            registry.approve("RC_REG", approved_by="ops.rama")

        dispatched = registry.dispatch("RC_REG", today=date(2026, 2, 18))
        assert dispatched.state is FilingState.FILED
        assert dispatched.dispatched is True
        with pytest.raises(ValueError, match="human sign-off"):
            registry.dispatch("RC_REG", today=date(2026, 2, 18))

    def test_reject_path_closes_pending_filing(self):
        registry = MsmedFilingRegistry()
        ladder = MsmedEscalationLadder()
        old = _make_receivable("RC_REJ")
        filing = ladder.request_conciliation_filing(old, date(2026, 2, 16))
        registry.record(filing)
        rejected = registry.reject("RC_REJ", remark="buyer paid", today=date(2026, 2, 17))
        assert rejected.state is FilingState.REJECTED
        with pytest.raises(ValueError, match="human sign-off"):
            registry.dispatch("RC_REJ", today=date(2026, 2, 17))

    def test_no_auto_file_code_path(self):
        ladder = MsmedEscalationLadder()
        old = _make_receivable("RC_NOAUTO")
        filing = ladder.request_conciliation_filing(old, date(2026, 2, 16))
        # No method marks a PENDING_SIGNOFF packet as dispatched.
        assert filing.dispatched is False
        assert filing.state is FilingState.PENDING_SIGNOFF
        assert not hasattr(filing, "dispatched_by")
        assert not hasattr(ladder, "auto_file")


# ── 3. Non-MSME buyers ───────────────────────────────────────────────────


class TestNonMsmeBuyer:
    def test_non_registered_supplier_gets_standard_terms_label(self):
        receivable = _make_receivable("RC_CORP", registered=False)
        ladder = MsmedEscalationLadder()
        state = ladder.state_for(receivable, date(2026, 6, 16))
        assert state.statutory_applies is False
        assert "standard commercial terms apply — §16 does not apply" in state.applicable_label
        assert state.interest_paise == 0
        assert state.total_claim_paise == PRINCIPAL_PAISE
        assert state.rung is None

    def test_inspector_surfaces_non_msme_label(self):
        tracer = _make_tracer("RC_CORP2", root_cause="overdue_invoice")
        configure(
            tracer=tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=[],
            receivable_profiles={"RC_CORP2": _make_receivable("RC_CORP2", registered=False)},
        )
        data = client.get("/api/cases/RC_CORP2/msmed/status").json()
        assert data["applicable"] is False
        assert data["interest_paise"] == 0
        assert "§16 does not apply" in data["applicable_label"]
        assert "rung" not in data


# ── 4. Statutory notice generator ───────────────────────────────────────


class FakeLlmGood:
    """An LLM that localizes wording while preserving backend truth."""

    def __init__(self, spec):
        self._spec = spec

    def generate_json(self, prompt: str) -> dict:
        s = self._spec
        return {
            "subject": "Interest Advisory — invoice INV-B2B",
            "body": (
                f"Dear {s.buyer_name}, invoice {s.invoice_id} dated "
                f"{s.invoice_date.isoformat()} (statutory due date "
                f"{s.statutory_due_date.isoformat()}) of Rs {FORMAT_INR(s.principal_paise)} "
                f"has accrued statutory interest of Rs {FORMAT_INR(s.interest_paise)} as of "
                f"{s.notice_date.isoformat()}. Total claim Rs {FORMAT_INR(s.claim_paise)}."
            ),
        }


class FakeLlmBad:
    """An LLM that invents figures — must be rejected by the §2.3 guard."""

    def generate_json(self, prompt: str) -> dict:
        return {
            "subject": "Notice",
            "body": (
                "Dear Acme, the total claim is Rs 9,99,999 and the due date was "
                "2026-12-31. Please pay immediately."
            ),
        }


class TestStatutoryNoticeGenerator:
    def setup_method(self):
        self.receivable = _make_receivable("RC_NOTICE")
        run = compute_interest_run(PRINCIPAL_PAISE, INVOICE_DATE, date(2026, 6, 16), RATE)
        self.spec = build_notice_spec(
            self.receivable,
            interest_paise=run.accrued_interest_paise,
            claim_paise=run.total_statutory_claim_paise,
            statutory_rate_percent=run.statutory_rate_percent,
            notice_date=date(2026, 6, 16),
        )

    def test_rungs_1_has_no_notice(self):
        gen = StatutoryNoticeGenerator()
        spec = build_notice_spec(
            self.receivable,
            interest_paise=0,
            claim_paise=PRINCIPAL_PAISE,
            statutory_rate_percent=18.0,
            notice_date=date(2026, 1, 5),
        )
        with pytest.raises(ValueError, match="rungs 2–4 only"):
            gen.generate(spec, "en")

    def test_bad_register_rejected(self):
        gen = StatutoryNoticeGenerator()
        with pytest.raises(ValueError, match="Unknown register"):
            gen.generate(self.spec, "xx")

    def test_fallback_notice_valid_for_both_registers(self):
        gen = StatutoryNoticeGenerator()  # no LLM → deterministic fallback
        for register in ("en", "hi-en"):
            draft = gen.generate(self.spec, register)
            assert draft.source == "fallback"
            assert draft.attempts == 1
            assert draft.rung is self.spec.rung
            errors = validate_draft(build_notice_context(self.spec, ""), draft.subject, draft.body)
            assert errors == [], (
                f"{register} fallback notice contains an invented figure/date: {errors}"
            )

    def test_hallucinated_llm_draft_forced_to_fallback(self):
        gen = StatutoryNoticeGenerator(llm_client=FakeLlmBad())
        draft = gen.generate(self.spec, "en")
        assert draft.source == "fallback", "invented figures must be rejected, not sent"
        assert "9,99,999" not in draft.body
        assert "2026-12-31" not in draft.body

    def test_backend_truth_passes_validation_and_is_used(self):
        gen = StatutoryNoticeGenerator(llm_client=FakeLlmGood(self.spec), max_attempts=2)
        draft = gen.generate(self.spec, "en")
        assert draft.source == "llm"
        assert draft.attempts == 1
        errors = validate_draft(build_notice_context(self.spec, ""), draft.subject, draft.body)
        assert errors == []

    def test_repeated_llm_failures_still_yield_fallback(self):
        calls = {"n": 0}

        class FlakyLlm:
            def generate_json(self, prompt):
                calls["n"] += 1
                return {}

        gen = StatutoryNoticeGenerator(llm_client=FlakyLlm(), max_attempts=3)
        draft = gen.generate(self.spec, "hi-en")
        assert draft.source == "fallback"
        assert calls["n"] == 3
        assert draft.body.strip() != ""


# ── 5. Inspector + API wiring ────────────────────────────────────────────


class TestMsmedApi:
    def setup_method(self):
        self.today = datetime.now(timezone.utc).date()
        self.old_invoice = self.today - timedelta(days=60)  # Day 61 → Rung 4
        self.young_invoice = self.today - timedelta(days=10)  # Day 11 → Rung 1

        self.case_rung4 = "RC_API_R4"
        self.case_rung1 = "RC_API_R1"
        self.case_non_b2b = "RC_API_NONB2B"

        self.tracer = DecisionTracer()
        _make_tracer(self.case_rung4, tracer=self.tracer, root_cause="overdue_invoice")
        _make_tracer(self.case_rung1, tracer=self.tracer, root_cause="overdue_invoice")
        _make_tracer(self.case_non_b2b, tracer=self.tracer, root_cause="mandate_failure")

        configure(
            tracer=self.tracer,
            audit=AuditLogger(),
            prevention=PreventionLog(),
            outbox=[],
            msmed_calculator=MsmedInterestCalculator(),
            msmed_ladder=MsmedEscalationLadder(),
            msmed_notices=StatutoryNoticeGenerator(),
            msmed_filings=MsmedFilingRegistry(),
            receivable_profiles={
                self.case_rung4: _make_receivable(self.case_rung4, invoice_date=self.old_invoice),
                self.case_rung1: _make_receivable(self.case_rung1, invoice_date=self.young_invoice),
            },
        )

    def test_status_read_only_returns_rung4_live_interest_and_notices(self):
        resp = client.get(f"/api/cases/{self.case_rung4}/msmed/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["applicable"] is True
        assert body["rung_number"] == 4
        assert body["rung_label"] == "MSME Samadhaan conciliation filing"
        assert body["days_since_invoice"] == 61
        assert body["interest_paise"] > 0
        assert body["total_statutory_claim_paise"] == PRINCIPAL_PAISE + body["interest_paise"]
        assert body["statutory_rate_percent"] == 18.75
        assert "en" in body["notice"] and "hi-en" in body["notice"]
        assert body["notice"]["en"]["source"] == "fallback"

    def test_status_post_refresh_works(self):
        resp = client.post(f"/api/cases/{self.case_rung4}/msmed/status")
        assert resp.status_code == 200
        assert resp.json()["rung_number"] == 4

    def test_rung1_case_has_no_notice(self):
        resp = client.get(f"/api/cases/{self.case_rung1}/msmed/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rung_number"] == 1
        assert "notice" not in body
        assert body["interest_paise"] == 0

    def test_non_b2b_case_has_no_statutory_context(self):
        resp = client.get(f"/api/cases/{self.case_non_b2b}/msmed/status")
        assert resp.status_code == 404

    def test_full_conciliation_workflow(self):
        # request → approve → dispatch (each an explicit human step)
        req = client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "request", "actor": "ops.rama"},
        )
        assert req.status_code == 200
        assert req.json()["filing"]["state"] == "PENDING_SIGNOFF"
        assert req.json()["filing"]["filing_reference"].startswith("SAMADHAAN-")

        # cannot skip the human sign-off gate
        early = client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "dispatch", "actor": "ops.rama"},
        )
        assert early.status_code == 409
        assert "human sign-off" in early.json()["detail"]

        appr = client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "approve", "actor": "ops.rama"},
        )
        assert appr.status_code == 200
        assert appr.json()["filing"]["state"] == "APPROVED"
        assert appr.json()["filing"]["approved_by"] == "ops.rama"

        disp = client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "dispatch", "actor": "ops.rama"},
        )
        assert disp.status_code == 200
        assert disp.json()["filing"]["state"] == "FILED"
        assert disp.json()["filing"]["dispatched"] is True

    def test_duplicate_request_conflicts(self):
        client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "request"},
        )
        again = client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "request"},
        )
        assert again.status_code == 409
        assert "already exists" in again.json()["detail"]

    def test_request_before_day_45_refused(self):
        resp = client.post(
            f"/api/cases/{self.case_rung1}/msmed/conciliation",
            json={"action": "request"},
        )
        assert resp.status_code == 409
        assert "Day 45" in resp.json()["detail"]

    def test_reject_workflow_on_second_case(self):
        client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "request"},
        )
        rej = client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "reject", "remark": "buyer wired funds"},
        )
        assert rej.status_code == 200
        assert rej.json()["filing"]["state"] == "REJECTED"

    def test_unknown_action_rejected(self):
        resp = client.post(
            f"/api/cases/{self.case_rung4}/msmed/conciliation",
            json={"action": "delete"},
        )
        assert resp.status_code == 400


# ── 6. Decision packet surfaces statutory + narrative ────────────────────


class TestDecisionPacketStatutory:
    def test_overdue_case_carries_statutory_block(self):
        tracer = DecisionTracer()
        _make_tracer("RC_PKT", tracer=tracer)
        # uses _derive_receivable (no stored profile)
        from app.dashboard.case_inspector import CaseInspector

        inspector = CaseInspector(tracer=tracer, audit=AuditLogger())
        packet = inspector.build_packet("RC_PKT")
        assert packet is not None
        assert packet.statutory is not None
        assert packet.statutory["profile_source"] == "demo_derived"
        assert packet.statutory["days_since_invoice"] >= 1
        assert packet.case_narrative and len(packet.case_narrative) > 20
        assert "RC_PKT" in packet.case_narrative

    def test_non_b2b_case_has_no_statutory_block(self):
        tracer = DecisionTracer()
        _make_tracer("RC_PKT2", tracer=tracer, root_cause="mandate_failure")
        from app.dashboard.case_inspector import CaseInspector

        inspector = CaseInspector(tracer=tracer, audit=AuditLogger())
        packet = inspector.build_packet("RC_PKT2")
        assert packet.statutory is None
        assert packet.case_narrative and "mandate" in packet.case_narrative.lower()

    def test_packet_to_dict_exposes_why_this_case(self):
        tracer = DecisionTracer()
        _make_tracer("RC_PKT3", tracer=tracer)
        from app.dashboard.case_inspector import CaseInspector, packet_to_dict

        packet = CaseInspector(tracer=tracer, audit=AuditLogger()).build_packet("RC_PKT3")
        data = packet_to_dict(packet)
        assert data["why_this_case"] == data["case_narrative"]
        assert "statutory" in data