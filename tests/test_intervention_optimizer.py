"""Tests for app/optimizer/ — Track C step 1.

Covers: InterventionOptimizer (§1.1 equation), RecoveryEconomics, ContactFatigue,
ChannelAffinity, OptimalTiming, ActionSuppression, RecoveryBudget,
MultiObligationOptimizer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.contracts import Action, CandidateAction, RecoveryStopReason
from app.core.obligation import Obligation, ObligationStatus
from app.core.recovery_case import RecoveryCase, UpliftSegment
from app.optimizer.action_suppression import ActionSuppressor
from app.optimizer.channel_affinity import (
    ChannelAffinityModel,
    ChannelAffinityStore,
)
from app.optimizer.contact_fatigue import (
    ContactFatigue,
    ContactFatigueEngine,
    FatigueAssessment,
)
from app.optimizer.intervention_optimizer import (
    InterventionOptimizer,
    OptimizationRecommendation,
)
from app.optimizer.multi_obligation import (
    CustomerContactBudget,
    MultiObligationOptimizer,
)
from app.optimizer.optimal_timing import (
    OptimalTimingModel,
    SalaryDayHeuristic,
    TimingRecommendation,
)
from app.optimizer.recovery_budget import RecoveryBudgetOptimizer
from app.optimizer.recovery_economics import (
    CommunicationCostTable,
    RecoveryEconomics,
)
from app.revenue_risk.uplift_model import UpliftEstimates

# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


def _obligation(
    oid: str = "OBL_1",
    original: int = 5000000,
    paid: int = 0,
    status: ObligationStatus = ObligationStatus.OPEN,
) -> Obligation:
    o = Obligation(obligation_id=oid, type="payment", original_amount=original)
    if paid > 0:
        o.record_payment(paid)
    if status == ObligationStatus.DISPUTED:
        o.mark_disputed()
    elif status == ObligationStatus.BLOCKED:
        o.block()
    return o


def _case(
    case_id: str = "RC_1",
    customer_id: str = "CUST_1",
    obligations: list[Obligation] | None = None,
    fraud_score: float = 0.0,
    dispute_score: float = 0.0,
    natural_pay: float = 0.3,
    communications: list[dict] | None = None,
    audit_events: list[dict] | None = None,
    payment_links: list[dict] | None = None,
    ptps: list[dict] | None = None,
    decisions: list[dict] | None = None,
) -> RecoveryCase:
    rc = RecoveryCase(
        case_id=case_id,
        customer_id=customer_id,
        fraud_score=fraud_score,
        dispute_score=dispute_score,
        natural_pay_probability=natural_pay,
    )
    for o in obligations or [_obligation()]:
        rc.add_obligation(o)
    if communications:
        for c in communications:
            rc.add_communication(c)
    if audit_events:
        for e in audit_events:
            rc.add_audit_event(e)
    if payment_links:
        for pl in payment_links:
            rc.add_payment_link(pl)
    if ptps:
        for p in ptps:
            rc.add_ptp(p)
    if decisions:
        for d in decisions:
            rc.add_decision(d)
    return rc


def _estimates(
    baseline: float = 0.30,
    uplifts: dict[Action, float] | None = None,
    segment: UpliftSegment | str = UpliftSegment.PERSUADABLE,
) -> UpliftEstimates:
    """Build a mock UpliftEstimates with sensible defaults."""
    default_uplifts = {
        Action.NO_ACTION: 0.0,
        Action.WAIT: 0.02,
        Action.RETRY_SAME_METHOD: 0.05,
        Action.RETRY_ALTERNATE_METHOD: 0.04,
        Action.SEND_PAYMENT_LINK: 0.37,
        Action.SEND_SMS: 0.09,
        Action.SEND_EMAIL: 0.05,
        Action.SEND_WHATSAPP: 0.15,
        Action.VOICE_CALL: 0.19,
        Action.REQUEST_PAYMENT_METHOD_UPDATE: 0.08,
        Action.OFFER_PARTIAL_PAYMENT: 0.10,
        Action.CREATE_PTP: 0.06,
        Action.HUMAN_ESCALATION: 0.03,
        Action.BLOCK: 0.0,
    }
    if uplifts:
        default_uplifts.update(uplifts)

    probs = {a: baseline + u for a, u in default_uplifts.items()}
    return UpliftEstimates(
        baseline_natural_probability=baseline,
        per_action_probability=probs,
        per_action_uplift=default_uplifts,
        uplift_segment=segment,
    )


# ======================================================================
# RecoveryEconomics
# ======================================================================


class TestRecoveryEconomics:
    def test_net_economic_value_basic(self):
        econ = RecoveryEconomics()
        nev = econ.net_economic_value(
            expected_recovery=5000,
            communication_cost=5,
            operational_cost=0,
            risk_penalty=10,
            cx_penalty=100,
        )
        assert nev == 5000 - 5 - 0 - 10 - 100 == 4885

    def test_no_action_is_zero_baseline(self):
        econ = RecoveryEconomics()
        candidates = econ.score_candidates(_estimates(), amount_remaining_paise=5000000)
        no_action = next(c for c in candidates if c.action == Action.NO_ACTION)
        assert no_action.expected_recovery_paise == 0
        assert no_action.communication_cost_paise == 0
        assert no_action.cx_penalty_paise == 0
        assert no_action.economic_score == 0.0

    def test_all_actions_scored(self):
        econ = RecoveryEconomics()
        candidates = econ.score_candidates(_estimates(), amount_remaining_paise=5000000)
        actions_scored = {c.action for c in candidates}
        assert actions_scored == set(Action)

    def test_payment_link_high_score(self):
        """SEND_PAYMENT_LINK has 37% uplift — should have highest expected recovery."""
        econ = RecoveryEconomics()
        candidates = econ.score_candidates(_estimates(), amount_remaining_paise=5000000)
        by_action = {c.action: c for c in candidates}
        link = by_action[Action.SEND_PAYMENT_LINK]
        sms = by_action[Action.SEND_SMS]
        assert link.expected_recovery_paise > sms.expected_recovery_paise

    def test_communication_cost_table_override(self):
        table = CommunicationCostTable(overrides={"SEND_SMS": 999})
        assert table.cost_paise("SEND_SMS") == 999
        assert table.cost_paise("NO_ACTION") == 0


# ======================================================================
# ContactFatigueEngine
# ======================================================================


class TestContactFatigue:
    def test_low_fatigue(self):
        engine = ContactFatigueEngine()
        result = engine.score(
            {
                "contacts_last_1h": 0,
                "contacts_last_24h": 1,
                "contacts_last_7d": 2,
                "ignored_contacts": 0,
                "negative_replies": 0,
                "opt_outs": 0,
                "complaints": 0,
                "successful_contacts": 1,
                "total_contacts": 2,
            }
        )
        assert result.level == ContactFatigue.LOW
        assert not engine.should_pause_automation(result)

    def test_opt_out_immediate_severe(self):
        engine = ContactFatigueEngine()
        result = engine.score({"opt_outs": 1})
        assert result.level == ContactFatigue.SEVERE
        assert engine.should_pause_automation(result)

    def test_complaint_immediate_severe(self):
        engine = ContactFatigueEngine()
        result = engine.score({"complaints": 1})
        assert result.level == ContactFatigue.SEVERE
        assert engine.should_pause_automation(result)

    def test_high_frequency_high_fatigue(self):
        engine = ContactFatigueEngine()
        result = engine.score(
            {
                "contacts_last_1h": 3,
                "contacts_last_24h": 6,
                "contacts_last_7d": 15,
                "ignored_contacts": 12,
                "negative_replies": 2,
                "total_contacts": 15,
            }
        )
        assert result.level in {ContactFatigue.HIGH, ContactFatigue.SEVERE}
        assert engine.should_pause_automation(result)

    def test_should_pause_high_and_severe(self):
        engine = ContactFatigueEngine()
        assert engine.should_pause_automation(
            FatigueAssessment(0.6, ContactFatigue.HIGH, ())
        )
        assert engine.should_pause_automation(
            FatigueAssessment(1.0, ContactFatigue.SEVERE, ())
        )
        assert not engine.should_pause_automation(
            FatigueAssessment(0.2, ContactFatigue.LOW, ())
        )
        assert not engine.should_pause_automation(
            FatigueAssessment(0.4, ContactFatigue.MEDIUM, ())
        )


# ======================================================================
# ChannelAffinityModel
# ======================================================================


class TestChannelAffinity:
    def test_default_priors(self):
        model = ChannelAffinityModel()
        affinity = model.affinity_for(_case())
        assert len(affinity.per_channel_conversion) > 0
        assert affinity.preferred_channel is not None

    def test_learning_from_outcome(self):
        store = ChannelAffinityStore()
        model = ChannelAffinityModel(store=store)

        from app.contracts import ContactChannel

        # Record 10 successes on WhatsApp
        for _ in range(10):
            model.update_from_outcome("CUST_1", ContactChannel.WHATSAPP, success=True)

        affinity = model.affinity_for(_case())
        # WhatsApp should have higher conversion than SMS (which has no history)
        assert (
            affinity.per_channel_conversion[ContactChannel.WHATSAPP]
            > affinity.per_channel_conversion[ContactChannel.SMS]
        )


# ======================================================================
# OptimalTimingModel
# ======================================================================


class TestOptimalTiming:
    def test_returns_recommendation(self):
        model = OptimalTimingModel()
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        rec = model.recommend({"failure_reason": "insufficient_funds"}, now)
        assert isinstance(rec, TimingRecommendation)
        assert rec.predicted_conversion > 0
        assert rec.best_send_at >= now

    def test_salary_day_heuristic(self):
        h = SalaryDayHeuristic(default_salary_day=1)
        assert h.compute("C1") == 1
        assert h.compute("C1", salary_day=15) == 15

    def test_business_hours_clamping(self):
        model = OptimalTimingModel()
        # 11 PM IST — should clamp to next day 8 AM
        late_night = datetime(2026, 9, 2, 23, 0, 0, tzinfo=timezone.utc)
        rec = model.recommend({"failure_reason": "bank_timeout"}, late_night)
        assert rec.best_send_at.hour >= 8


# ======================================================================
# ActionSuppressor
# ======================================================================


class TestActionSuppression:
    def test_no_action_never_suppressed(self):
        s = ActionSuppressor()
        verdict = s.check(_case(), Action.NO_ACTION)
        assert not verdict.suppressed

    def test_already_paid_suppresses_outbound(self):
        o = Obligation(
            obligation_id="OBL_1",
            type="payment",
            original_amount=1000,
            paid_amount=1000,
        )
        case = _case(obligations=[o])
        s = ActionSuppressor()
        verdict = s.check(case, Action.SEND_SMS)
        assert verdict.suppressed
        assert "already_paid" in verdict.suppress_reasons

    def test_disputed_suppresses(self):
        o = _obligation(status=ObligationStatus.DISPUTED)
        case = _case(obligations=[o])
        s = ActionSuppressor()
        verdict = s.check(case, Action.SEND_PAYMENT_LINK)
        assert verdict.suppressed
        assert "active_dispute" in verdict.suppress_reasons

    def test_active_link_suppresses_duplicate(self):
        case = _case(payment_links=[{"state": "CREATED"}])
        s = ActionSuppressor()
        verdict = s.check(case, Action.SEND_PAYMENT_LINK)
        assert verdict.suppressed
        assert "active_payment_link" in verdict.suppress_reasons

    def test_opt_out_suppresses_outbound(self):
        case = _case(communications=[{"action": "SEND_SMS", "opt_out": True}])
        s = ActionSuppressor()
        verdict = s.check(case, Action.SEND_SMS)
        assert verdict.suppressed
        assert "customer_opted_out" in verdict.suppress_reasons

    def test_ptp_hold_suppresses_outbound(self):
        o = _obligation()
        o.create_ptp()  # PTP_HOLD
        case = _case(obligations=[o])
        s = ActionSuppressor()
        verdict = s.check(case, Action.SEND_SMS)
        assert verdict.suppressed
        assert "active_ptp" in verdict.suppress_reasons

    def test_human_escalation_always_allowed(self):
        s = ActionSuppressor()
        verdict = s.check(_case(), Action.HUMAN_ESCALATION)
        assert not verdict.suppressed

    def test_suppressed_actions_list(self):
        o = Obligation(
            obligation_id="OBL_1",
            type="payment",
            original_amount=1000,
            paid_amount=1000,
        )
        case = _case(obligations=[o])
        s = ActionSuppressor()
        suppressed = s.suppressed_actions(case)
        assert len(suppressed) > 0
        assert "NO_ACTION" not in suppressed  # exempt


# ======================================================================
# RecoveryBudgetOptimizer
# ======================================================================


class TestRecoveryBudget:
    def test_basic_allocation(self):
        optimizer = RecoveryBudgetOptimizer()
        cases = [
            {
                "case_id": "C1",
                "action": "SEND_SMS",
                "incremental_recovery_paise": 50000,
                "cost_paise": 5,
            },
            {
                "case_id": "C2",
                "action": "VOICE_CALL",
                "incremental_recovery_paise": 70000,
                "cost_paise": 600,
            },
            {
                "case_id": "C3",
                "action": "SEND_SMS",
                "incremental_recovery_paise": 30000,
                "cost_paise": 5,
            },
        ]
        result = optimizer.allocate(cases, daily_budget_paise=100)
        assert "C1" in result.chosen_cases  # High ROI
        assert "C3" in result.chosen_cases  # High ROI, fits budget
        assert result.projected_cost_paise <= 100

    def test_budget_exhaustion(self):
        optimizer = RecoveryBudgetOptimizer()
        cases = [
            {
                "case_id": "C1",
                "action": "VOICE_CALL",
                "incremental_recovery_paise": 100000,
                "cost_paise": 600,
            },
            {
                "case_id": "C2",
                "action": "VOICE_CALL",
                "incremental_recovery_paise": 80000,
                "cost_paise": 600,
            },
        ]
        result = optimizer.allocate(cases, daily_budget_paise=700)
        # Only one fits
        assert len([c for c in result.chosen_cases]) == 1
        assert len(result.rejected_cases) == 1

    def test_free_actions_always_included(self):
        optimizer = RecoveryBudgetOptimizer()
        cases = [
            {
                "case_id": "C1",
                "action": "NO_ACTION",
                "incremental_recovery_paise": 0,
                "cost_paise": 0,
            },
            {
                "case_id": "C2",
                "action": "SEND_SMS",
                "incremental_recovery_paise": 50000,
                "cost_paise": 5,
            },
        ]
        result = optimizer.allocate(cases, daily_budget_paise=10)
        assert "C1" in result.chosen_cases


# ======================================================================
# MultiObligationOptimizer
# ======================================================================


class TestMultiObligation:
    def test_selects_highest_value_obligation(self):
        o_small = Obligation(
            obligation_id="OBL_S", type="payment", original_amount=1000000
        )
        o_large = Obligation(
            obligation_id="OBL_L", type="invoice", original_amount=5000000
        )
        case1 = _case(case_id="RC_1", obligations=[o_small])
        case2 = _case(case_id="RC_2", obligations=[o_large])

        opt = MultiObligationOptimizer()
        decision = opt.optimize_customer([case1, case2], max_contacts_per_day=2)
        assert decision.selected_obligation_id == "OBL_L"

    def test_budget_exhaustion_no_action(self):
        budget = CustomerContactBudget(default_daily_limit=1)
        budget.consume("CUST_1", 1)

        opt = MultiObligationOptimizer(contact_budget=budget)
        decision = opt.optimize_customer([_case()], max_contacts_per_day=1)
        assert decision.selected_action == Action.NO_ACTION
        assert decision.contact_budget_remaining == 0

    def test_all_terminal_no_action(self):
        o = Obligation(
            obligation_id="OBL_1",
            type="payment",
            original_amount=1000,
            paid_amount=1000,
        )
        case = _case(obligations=[o])
        opt = MultiObligationOptimizer()
        decision = opt.optimize_customer([case])
        assert decision.selected_action == Action.NO_ACTION

    def test_contact_budget_tracks_correctly(self):
        budget = CustomerContactBudget(default_daily_limit=3)
        assert budget.remaining_today("C1") == 3
        assert budget.consume("C1")
        assert budget.remaining_today("C1") == 2
        assert budget.consume("C1")
        assert budget.consume("C1")
        assert not budget.consume("C1")
        budget.reset("C1")
        assert budget.remaining_today("C1") == 3


# ======================================================================
# InterventionOptimizer (the central equation §1.1)
# ======================================================================


class TestInterventionOptimizer:
    def test_selects_highest_nev_action(self):
        """argmax should pick the action with highest net economic value."""
        opt = InterventionOptimizer()
        case = _case()
        est = _estimates()
        rec = opt.optimize(case, est)
        assert isinstance(rec, OptimizationRecommendation)
        # SEND_PAYMENT_LINK has the highest uplift (0.37) → should win
        assert rec.selected_action == Action.SEND_PAYMENT_LINK
        assert not rec.stopped

    def test_no_action_always_candidate(self):
        opt = InterventionOptimizer()
        rec = opt.optimize(_case(), _estimates())
        actions = {c.action for c in rec.candidates}
        assert Action.NO_ACTION in actions

    def test_sure_thing_gets_no_action(self):
        """SURE_THING segment → NO_ACTION regardless of uplift scores."""
        opt = InterventionOptimizer()
        est = _estimates(baseline=0.85, segment=UpliftSegment.SURE_THING)
        rec = opt.optimize(_case(), est)
        assert rec.selected_action == Action.NO_ACTION
        assert not rec.stopped

    def test_sleeping_dog_gets_no_action(self):
        """SLEEPING_DOG segment → NO_ACTION because intervention hurts."""
        opt = InterventionOptimizer()
        est = _estimates(baseline=0.30, segment=UpliftSegment.SLEEPING_DOG)
        rec = opt.optimize(_case(), est)
        assert rec.selected_action == Action.NO_ACTION

    def test_disputed_obligation_compliance_stop(self):
        """Dispute on any obligation → COMPLIANCE_RISK stop → NO_ACTION."""
        o = _obligation(status=ObligationStatus.DISPUTED)
        case = _case(obligations=[o])
        opt = InterventionOptimizer()
        rec = opt.optimize(case, _estimates())
        assert rec.stopped
        assert rec.stop_reason == RecoveryStopReason.COMPLIANCE_RISK
        assert rec.selected_action == Action.NO_ACTION

    def test_fraud_block_compliance_stop(self):
        """Blocked obligation → COMPLIANCE_RISK stop."""
        o = _obligation(status=ObligationStatus.BLOCKED)
        case = _case(obligations=[o])
        opt = InterventionOptimizer()
        rec = opt.optimize(case, _estimates())
        assert rec.stopped
        assert rec.stop_reason == RecoveryStopReason.COMPLIANCE_RISK

    def test_high_fraud_score_compliance_stop(self):
        """fraud_score ≥ 0.8 → COMPLIANCE_RISK stop."""
        case = _case(fraud_score=0.85)
        opt = InterventionOptimizer()
        rec = opt.optimize(case, _estimates())
        assert rec.stopped
        assert rec.stop_reason == RecoveryStopReason.COMPLIANCE_RISK

    def test_opt_out_customer_stop(self):
        """Customer opt-out → CUSTOMER stop."""
        case = _case(audit_events=[{"type": "OPT_OUT"}])
        opt = InterventionOptimizer()
        rec = opt.optimize(case, _estimates())
        assert rec.stopped
        assert rec.stop_reason == RecoveryStopReason.CUSTOMER

    def test_wrong_person_customer_stop(self):
        """Wrong-person flag → CUSTOMER stop."""
        case = _case(audit_events=[{"type": "WRONG_PERSON"}])
        opt = InterventionOptimizer()
        rec = opt.optimize(case, _estimates())
        assert rec.stopped
        assert rec.stop_reason == RecoveryStopReason.CUSTOMER

    def test_financial_stop_low_expected_recovery(self):
        """Small uplift on small amount → financial stop when best action's
        expected recovery is positive but below min threshold (500 paise).
        """
        opt = InterventionOptimizer(min_incremental_paise=500)
        # 1% uplift on ₹400 (40000p) = 400p expected recovery — below 500p threshold
        small_uplifts = {a: 0.01 for a in Action}
        small_uplifts[Action.NO_ACTION] = 0.0
        small_uplifts[Action.BLOCK] = 0.0
        small_uplifts[Action.WAIT] = 0.005
        est = _estimates(
            baseline=0.30, uplifts=small_uplifts, segment=UpliftSegment.LOST_CAUSE
        )
        o = _obligation(original=40000)  # ₹400
        case = _case(obligations=[o])
        rec = opt.optimize(case, est)
        assert rec.stopped
        assert rec.stop_reason == RecoveryStopReason.FINANCIAL
        assert rec.selected_action == Action.NO_ACTION

    def test_fatigue_blocks_outbound(self):
        """High contact fatigue → only WAIT/NO_ACTION/HUMAN_ESCALATION survive."""
        opt = InterventionOptimizer()
        # Many ignored contacts → HIGH fatigue
        comms = [{"action": "SEND_SMS"} for _ in range(10)]
        case = _case(communications=comms)
        est = _estimates()
        rec = opt.optimize(case, est)
        # Should select from {NO_ACTION, WAIT, HUMAN_ESCALATION}
        assert rec.selected_action in {
            Action.NO_ACTION,
            Action.WAIT,
            Action.HUMAN_ESCALATION,
        }

    def test_suppression_filters_duplicate_link(self):
        """Active payment link → SEND_PAYMENT_LINK is suppressed."""
        case = _case(payment_links=[{"state": "CREATED"}])
        opt = InterventionOptimizer()
        rec = opt.optimize(case, _estimates())
        # SEND_PAYMENT_LINK should be in rejected_reasons
        assert Action.SEND_PAYMENT_LINK in rec.rejected_reasons

    def test_all_recovered_suppresses_everything(self):
        """All obligations RECOVERED → suppression blocks all outbound."""
        o = Obligation(
            obligation_id="OBL_1",
            type="payment",
            original_amount=1000,
            paid_amount=1000,
        )
        case = _case(obligations=[o])
        opt = InterventionOptimizer()
        rec = opt.optimize(
            case, _estimates(baseline=0.95, segment=UpliftSegment.SURE_THING)
        )
        assert rec.selected_action == Action.NO_ACTION

    def test_rejected_reasons_populated(self):
        """Suppressed and segment-overridden actions should appear in rejected_reasons."""
        opt = InterventionOptimizer()
        est = _estimates(segment=UpliftSegment.SURE_THING)
        rec = opt.optimize(_case(), est)
        assert len(rec.rejected_reasons) > 0

    def test_economic_scores_dict_present(self):
        opt = InterventionOptimizer()
        rec = opt.optimize(_case(), _estimates())
        assert isinstance(rec.economic_scores, dict)
        assert Action.NO_ACTION in rec.economic_scores

    def test_build_candidates_returns_all_actions(self):
        opt = InterventionOptimizer()
        candidates = opt.build_candidates(_case(), _estimates())
        assert len(candidates) == len(Action)

    def test_select_best_negative_scores_prefers_no_action(self):
        """When all candidates have negative scores, NO_ACTION wins."""
        opt = InterventionOptimizer()
        candidates = [
            CandidateAction(Action.SEND_SMS, 0, 1000, 0, 0, 0, -1000.0),
            CandidateAction(Action.NO_ACTION, 0, 0, 0, 0, 0, 0.0),
        ]
        best = opt.select_best(candidates)
        assert best.action == Action.NO_ACTION
