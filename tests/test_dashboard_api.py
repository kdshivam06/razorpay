"""Test suite: Dashboard data API — waterfall, lift, segments (§14, §12.1)."""

import pytest

from app.audit.decision_trace import DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.contracts import Action, CandidateAction, HumanPriority
from app.dashboard.api import DashboardApi
from app.executor.human_queue import HumanTask, HumanTaskQueue


def _trace(
    tr,
    case_id,
    *,
    segment="PERSUADABLE",
    natural=0.3,
    uplift=0.3,
    amount=100000,
    action=Action.SEND_SMS,
    candidates=None,
):
    tr.build(
        case_id=case_id,
        trigger_event="test",
        state="RISK_ASSESSED",
        root_cause="insufficient_funds_low",
        natural_payment_probability=natural,
        uplift_segment=segment,
        revenue_at_risk_paise=amount,
        candidate_actions=candidates
        or [
            CandidateAction(
                action=Action.SEND_SMS,
                expected_recovery_paise=amount,
                communication_cost_paise=100,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=0,
                economic_score=uplift,
            )
        ],
        selected_action=action,
        selected_economic_score=uplift,
        policy_checks_passed=[],
        policy_checks_failed=[],
        policy_gate_result="APPROVED",
        execution_result="SUCCESS",
    )


def test_waterfall_incremental_is_gross_minus_natural_and_net_subtracts_cost():
    tr = DecisionTracer()
    for i in range(10):
        _trace(tr, f"case_{i}")
    api = DashboardApi(tracer=tr)

    wf = api.waterfall()
    assert wf.revenue_at_risk_paise == 1_000_000
    assert wf.expected_natural_recovery_paise == 300_000
    # gross = natural (no candidates/outcomes) -> incremental 0 for the trace
    assert (
        wf.incremental_recovery_paise == wf.gross_recovery_opportunity_paise - 300_000
    )
    assert wf.incremental_net_recovery_paise == (
        wf.incremental_recovery_paise - wf.communication_cost_paise
    )


def test_uplift_segments_report_four_ordered_buckets_with_counts():
    tr = DecisionTracer()
    _trace(tr, "case_1", segment="SURE_THING")
    _trace(tr, "case_2", segment="SLEEPING_DOG")
    _trace(tr, "case_3", segment="PERSUADABLE")
    _trace(tr, "case_4", segment="LOST_CAUSE")
    api = DashboardApi(tracer=tr)

    buckets = api.uplift_segments()
    assert [b.segment for b in buckets] == [
        "SURE_THING",
        "PERSUADABLE",
        "LOST_CAUSE",
        "SLEEPING_DOG",
    ]
    assert sum(b.case_count for b in buckets) == 4


def test_control_vs_treatment_lift_includes_control_and_treatment():
    tr = DecisionTracer()
    _trace(tr, "case_t", natural=0.3)
    _trace(tr, "case_c_2", natural=0.2, action=None)  # case_c_2 -> control group
    api = DashboardApi(tracer=tr)

    lift = api.control_vs_treatment_lift()
    assert lift.treatment_cases + lift.control_cases == 2
    assert lift.control_payment_rate == pytest.approx(0.2)
    # treatment has no outcome -> uses natural probability 0.3
    assert lift.treatment_payment_rate == pytest.approx(0.3)
    assert lift.lift_pp == pytest.approx(10.0)


def test_contacts_avoided_counts_prevention_categories():
    tr = DecisionTracer()
    # SURE_THING case: pipeline correctly landed on NO_ACTION (no contact)
    _trace(tr, "case_1", segment="SURE_THING", action=None)
    prev = PreventionLog()
    prev.log("case_2", "VOICE_CALL", "cooldown active", "cooldown_active", 50_000)
    api = DashboardApi(tracer=tr, prevention=prev)

    avoided = api.contacts_avoided()
    assert avoided.total == 2
    assert avoided.money_prevented_paise == 50_000


def test_exception_queue_surfaces_pending_human_tasks():
    q = HumanTaskQueue()
    q.enqueue(
        HumanTask(
            task_id="t1",
            case_id="case_0",
            priority=HumanPriority.P2,
            action=Action.HUMAN_ESCALATION,
            proposed_reasoning="low confidence",
            sla_minutes=240,
        )
    )
    q.approve("t1")
    api = DashboardApi(human_queue=q)

    assert api.exception_queue() == []

    q.enqueue(
        HumanTask(
            task_id="t2",
            case_id="case_1",
            priority=HumanPriority.P1,
            action=Action.BLOCK,
            proposed_reasoning="fraud suspicion",
            sla_minutes=60,
        )
    )
    queue = api.exception_queue()
    assert len(queue) == 1
    assert queue[0].case_id == "case_1"
    assert queue[0].priority == "P1"


def test_scorecard_exposes_key_metrics():
    tr = DecisionTracer()
    _trace(tr, "case_1", segment="SURE_THING")
    api = DashboardApi(tracer=tr)

    card = api.scorecard()
    assert "incremental_recovery_paise" in card
    assert "net_recovery_paise" in card
    assert "recovery_lift_pp" in card
    assert "contacts_avoided" in card
    assert "breakdown" in card
    assert "pending" in card["breakdown"]
