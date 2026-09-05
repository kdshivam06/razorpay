"""Track E.5 — human-approval flag + action reasoning.

Verifies:
  - InterventionOptimizer computes requires_human_approval from
    reversibility.py / fraud_detector.py / blast_radius.py (§6.4, §7.7)
  - OptimizationRecommendation exposes recommended_action + reasoning
  - DecisionTracer stores requires_human_approval + reasoning
  - base_module.run() routes selection through the optimizer and the flag
    flows into the decision trace
  - /api/queue/auto-eligible and /api/queue/human-required split cases into
    the two buckets, and the dashboard serves the recommendation data
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.dashboard.api as dashboard_api
from app.audit.decision_trace import DecisionTracer
from app.contracts import Action, CandidateAction
from app.core.obligation import Obligation, ObligationStatus
from app.core.recovery_case import RecoveryCase, UpliftSegment
from app.dashboard.api import DashboardApi
from app.main import app
from app.optimizer.intervention_optimizer import InterventionOptimizer
from app.policy.blast_radius import BlastRadiusGuard
from app.revenue_risk.uplift_model import UpliftEstimates

client = TestClient(app)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

def _obligation(
    oid: str = "OBL_1",
    original: int = 5_000_000,
    status: ObligationStatus = ObligationStatus.OPEN,
) -> Obligation:
    o = Obligation(obligation_id=oid, type="payment", original_amount=original)
    if status == ObligationStatus.DISPUTED:
        o.mark_disputed()
    elif status == ObligationStatus.BLOCKED:
        o.block()
    return o


def _case(
    case_id: str = "RC_1",
    original: int = 5_000_000,
    fraud_score: float = 0.0,
    natural_pay: float = 0.30,
    status: ObligationStatus = ObligationStatus.OPEN,
    communications: list[dict] | None = None,
) -> RecoveryCase:
    rc = RecoveryCase(
        case_id=case_id,
        customer_id="CUST_1",
        fraud_score=fraud_score,
        natural_pay_probability=natural_pay,
    )
    rc.add_obligation(_obligation(original=original, status=status))
    for comm in communications or []:
        rc.add_communication(comm)
    return rc


def _estimates(
    baseline: float = 0.30,
    uplifts: dict[Action, float] | None = None,
    segment: UpliftSegment | str = UpliftSegment.PERSUADABLE,
) -> UpliftEstimates:
    """Build UpliftEstimates with a sensible per-action uplift profile."""
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


def _candidate(action: Action, score: float) -> CandidateAction:
    return CandidateAction(
        action=action,
        expected_recovery_paise=0,
        communication_cost_paise=0,
        operational_cost_paise=0,
        risk_penalty_paise=0,
        cx_penalty_paise=0,
        economic_score=score,
    )


# ──────────────────────────────────────────────────────────────────────
# Optimizer — requires_human_approval / recommended_action / reasoning
# ──────────────────────────────────────────────────────────────────────


class TestOptimizerHumanApprovalFlag:
    """§6.4 + §7.7: the flag derives from reversibility, fraud, blast radius."""

    def test_low_risk_sms_no_human_approval(self):
        """SMS for a small low-risk case → fully autonomous."""
        opt = InterventionOptimizer()
        case = _case()
        estimates = _estimates(
            uplifts={
                Action.SEND_PAYMENT_LINK: 0.05,
                Action.SEND_SMS: 0.12,
                Action.VOICE_CALL: 0.02,
                Action.SEND_WHATSAPP: 0.03,
            }
        )
        rec = opt.optimize(case, estimates)
        assert rec.selected_action == Action.SEND_SMS
        assert rec.requires_human_approval is False
        assert rec.reasoning
        assert "Requires human approval" not in rec.reasoning

    def test_high_impact_reversible_action_requires_human(self):
        """OFFER_PARTIAL_PAYMENT is HIGH impact (§6.4) → always human."""
        opt = InterventionOptimizer()
        case = _case()
        estimates = _estimates(
            uplifts={
                Action.OFFER_PARTIAL_PAYMENT: 0.40,
                Action.SEND_PAYMENT_LINK: 0.05,
                Action.SEND_SMS: 0.03,
            }
        )
        rec = opt.optimize(case, estimates)
        assert rec.selected_action == Action.OFFER_PARTIAL_PAYMENT
        assert rec.requires_human_approval is True
        assert "Requires human approval" in rec.reasoning

    def test_amount_above_threshold_requires_human(self):
        """§7.7: large amount (≥₹5L) needs human regardless of confidence."""
        opt = InterventionOptimizer()
        case = _case(original=60_000_000)  # ₹6L
        estimates = _estimates(
            uplifts={
                Action.SEND_PAYMENT_LINK: 0.37,
                Action.SEND_SMS: 0.05,
                Action.VOICE_CALL: 0.03,
            }
        )
        rec = opt.optimize(case, estimates)
        assert rec.selected_action == Action.SEND_PAYMENT_LINK
        assert rec.requires_human_approval is True
        assert "Requires human approval" in rec.reasoning

    def test_fraud_high_risk_requires_human(self):
        """fraud risk HIGH (0.60, below the 0.8 compliance stop) → human."""
        opt = InterventionOptimizer()
        case = _case(fraud_score=0.60)
        estimates = _estimates(
            uplifts={
                Action.SEND_SMS: 0.12,
                Action.SEND_PAYMENT_LINK: 0.10,
            }
        )
        rec = opt.optimize(case, estimates)
        assert rec.requires_human_approval is True
        assert "Requires human approval" in rec.reasoning

    def test_blast_radius_circuit_open_requires_human(self):
        """§7.5: circuit breaker open → outbound actions go to human review."""
        blast = BlastRadiusGuard()
        blast.open_circuit()
        opt = InterventionOptimizer(blast_radius=blast)
        case = _case()
        estimates = _estimates(
            uplifts={
                Action.SEND_SMS: 0.12,
                Action.SEND_PAYMENT_LINK: 0.10,
            }
        )
        rec = opt.optimize(case, estimates)
        assert rec.requires_human_approval is True
        assert "Requires human approval" in rec.reasoning

    def test_stopped_case_has_no_human_flag_and_stop_reasoning(self):
        """Dispute → compliance stop; NO_ACTION with stop reasoning."""
        opt = InterventionOptimizer()
        case = _case(status=ObligationStatus.DISPUTED)
        rec = opt.optimize(case, _estimates())
        assert rec.stopped is True
        assert rec.selected_action == Action.NO_ACTION
        assert rec.requires_human_approval is False
        assert "Stopped by" in rec.reasoning

    def test_recommended_action_aliases_selected_action(self):
        """Recommendation exposes recommended_action (the E.5 field)."""
        opt = InterventionOptimizer()
        rec = opt.optimize(_case(), _estimates())
        assert rec.recommended_action == rec.selected_action
        assert rec.recommended_action in (
            Action.SEND_PAYMENT_LINK,
            Action.SEND_SMS,
            Action.VOICE_CALL,
        )

    def test_reasoning_explains_action_choice_not_diagnosis(self):
        """E.5 reasoning is about the ACTION, distinct from E.4 diagnosis."""
        opt = InterventionOptimizer()
        case = _case()
        estimates = _estimates(
            uplifts={
                Action.SEND_PAYMENT_LINK: 0.37,
                Action.SEND_SMS: 0.05,
            }
        )
        rec = opt.optimize(case, estimates)
        assert "Payment link" in rec.reasoning or "SEND" in rec.reasoning
        assert "diagnos" not in rec.reasoning.lower()


# ──────────────────────────────────────────────────────────────────────
# DecisionTracer — flag + reasoning storage
# ──────────────────────────────────────────────────────────────────────


class TestDecisionTraceFields:
    def test_tracer_stores_human_approval_and_reasoning(self):
        tracer = DecisionTracer()
        trace = tracer.build(
            case_id="RC_TRACE",
            trigger_event="payment.failed",
            state="APPROVED",
            root_cause="insufficient_funds",
            natural_payment_probability=0.30,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=60_000_000,
            candidate_actions=[
                _candidate(Action.SEND_PAYMENT_LINK, 0.37),
                _candidate(Action.NO_ACTION, 0.0),
            ],
            selected_action=Action.SEND_PAYMENT_LINK,
            selected_economic_score=0.37,
            policy_gate_result="APPROVED",
            requires_human_approval=True,
            reasoning="Payment link offers highest uplift. Requires human approval.",
        )
        assert trace.requires_human_approval is True
        assert "Requires human approval" in trace.reasoning


# ──────────────────────────────────────────────────────────────────────
# base_module — selection routed through optimizer into the trace
# ──────────────────────────────────────────────────────────────────────


class TestModuleRoutesSelectionThroughOptimizer:
    def test_customer_revoked_mandate_reasoning_in_trace(self):
        """RBI compliance stop produces NO_ACTION with E.5 stop reasoning."""
        from app.executor.action_executor import ActionExecutor
        from app.modules.subscription_dunning import SubscriptionDunningModule
        from app.policy.policy_engine import PolicyEngine

        case = _case(
            case_id="RC_MANDATE",
            natural_pay=0.10,
            communications=[
                {
                    "error_source": "customer",
                    "error_description": "mandate revoked by customer",
                }
            ],
        )
        pe = PolicyEngine()
        tracer = DecisionTracer()
        module = SubscriptionDunningModule(
            policy_engine=pe,
            executor=ActionExecutor(policy_engine=pe),
            tracer=tracer,
        )
        result = module.run(case)
        assert result.selected_action == Action.NO_ACTION

        trace = tracer.latest_trace("RC_MANDATE")
        assert trace is not None
        assert trace.selected_action == Action.NO_ACTION
        assert trace.requires_human_approval is False
        assert trace.reasoning.startswith("Stopped by")
        assert trace.reasoning != trace.selection_reasoning


# ──────────────────────────────────────────────────────────────────────
# Queue split — auto-eligible vs human-required
# ──────────────────────────────────────────────────────────────────────


class TestQueueSplit:
    """GET /api/queue/auto-eligible and /api/queue/human-required split."""

    def _build_api(self) -> DashboardApi:
        tracer = DecisionTracer()
        candidates = [
            _candidate(Action.SEND_PAYMENT_LINK, 0.37),
            _candidate(Action.SEND_SMS, 0.09),
            _candidate(Action.NO_ACTION, 0.0),
        ]
        tracer.build(
            case_id="RC_AUTO",
            trigger_event="payment.failed",
            state="APPROVED",
            root_cause="insufficient_funds",
            natural_payment_probability=0.30,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=2_000_000,
            candidate_actions=candidates,
            selected_action=Action.SEND_SMS,
            selected_economic_score=0.09,
            selection_reasoning="SMS: expected_recovery=₹18000, score=0.09",
            policy_gate_result="APPROVED",
            requires_human_approval=False,
            reasoning="SMS provides cost-effective nudge (+9% uplift).",
        )
        tracer.build(
            case_id="RC_HUMAN",
            trigger_event="payment.failed",
            state="APPROVED",
            root_cause="insufficient_funds",
            natural_payment_probability=0.30,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=60_000_000,  # ₹6L → over ₹5L threshold
            candidate_actions=candidates,
            selected_action=Action.SEND_PAYMENT_LINK,
            selected_economic_score=0.37,
            selection_reasoning="P link: expected_recovery=₹2220000, score=0.37",
            policy_gate_result="APPROVED",
            requires_human_approval=True,
            reasoning="Payment link offers highest uplift. Requires human approval.",
        )
        tracer.build(
            case_id="RC_STOPPED",
            trigger_event="payment.failed",
            state="BLOCKED",
            root_cause="mandate_failure",
            natural_payment_probability=0.10,
            uplift_segment="LOST_CAUSE",
            revenue_at_risk_paise=2_000_000,
            candidate_actions=candidates,
            selected_action=None,
            policy_gate_result="BLOCKED",
            requires_human_approval=False,
            reasoning="All candidates blocked.",
        )
        return DashboardApi(tracer=tracer)

    def test_auto_eligible_only_contains_non_human_cases(self):
        api = self._build_api()
        auto = api.auto_eligible_queue()
        ids = {i.case_id for i in auto}
        assert ids == {"RC_AUTO"}
        assert all(i.requires_human is False for i in auto)

    def test_human_required_only_contains_human_cases(self):
        api = self._build_api()
        human = api.human_required_queue()
        ids = {i.case_id for i in human}
        assert ids == {"RC_HUMAN"}
        assert all(i.requires_human is True for i in human)

    def test_stopped_or_no_action_cases_excluded_from_both_queues(self):
        api = self._build_api()
        auto = {i.case_id for i in api.auto_eligible_queue()}
        human = {i.case_id for i in api.human_required_queue()}
        assert "RC_STOPPED" not in auto
        assert "RC_STOPPED" not in human

    def test_queue_items_expose_reasoning_and_recommended_action(self):
        api = self._build_api()
        human = api.human_required_queue()
        assert human
        item = human[0]
        assert item.recommended_action == "SEND_PAYMENT_LINK"
        assert "Requires human approval" in item.reasoning


class TestQueueEndpoints:
    """HTTP-level coverage for the two queue endpoints."""

    def setup_method(self):
        tracer = DecisionTracer()
        candidates = [
            _candidate(Action.SEND_PAYMENT_LINK, 0.37),
            _candidate(Action.SEND_SMS, 0.09),
        ]
        tracer.build(
            case_id="RC_HTTP_AUTO",
            trigger_event="payment.failed",
            state="APPROVED",
            root_cause="insufficient_funds",
            natural_payment_probability=0.30,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=2_000_000,
            candidate_actions=candidates,
            selected_action=Action.SEND_SMS,
            selected_economic_score=0.09,
            policy_gate_result="APPROVED",
            requires_human_approval=False,
            reasoning="SMS provides cost-effective nudge (+9% uplift).",
        )
        tracer.build(
            case_id="RC_HTTP_HUMAN",
            trigger_event="payment.failed",
            state="APPROVED",
            root_cause="insufficient_funds",
            natural_payment_probability=0.30,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=60_000_000,
            candidate_actions=candidates,
            selected_action=Action.SEND_PAYMENT_LINK,
            selected_economic_score=0.37,
            policy_gate_result="APPROVED",
            requires_human_approval=True,
            reasoning="Payment link offers highest uplift. Requires human approval.",
        )
        dashboard_api.configure(DashboardApi(tracer=tracer))

    def test_auto_eligible_endpoint_returns_200(self):
        response = client.get("/api/queue/auto-eligible")
        assert response.status_code == 200
        items = response.json()
        assert {i["case_id"] for i in items} == {"RC_HTTP_AUTO"}
        assert all(i["requires_human"] is False for i in items)
        assert all(i["reasoning"] for i in items)

    def test_human_required_endpoint_returns_200(self):
        response = client.get("/api/queue/human-required")
        assert response.status_code == 200
        items = response.json()
        assert {i["case_id"] for i in items} == {"RC_HTTP_HUMAN"}
        assert all(i["requires_human"] is True for i in items)

    def test_human_required_item_carries_recommendation_data(self):
        response = client.get("/api/queue/human-required")
        items = response.json()
        item = next(i for i in items if i["case_id"] == "RC_HTTP_HUMAN")
        assert item["recommended_action"] == "SEND_PAYMENT_LINK"
        assert item["amount_paise"] == 60_000_000
        assert item["uplift_segment"] == "PERSUADABLE"
        assert item["policy_gate_result"] == "APPROVED"
        assert "Requires human approval" in item["reasoning"]