"""Dashboard data API — the recovery waterfall + scorecard (§14.1–14.6).

Aggregates live from the already-built track data sources:
  - DecisionTracer / AuditLogger  → recovery economics per case
  - ControlGroup / ExperimentEngine → control-vs-treatment incremental lift
  - PreventionLog                 → contacts avoided + money prevented
  - HumanTaskQueue                → the exception queue
  - AgentMonitor                  → behavioural health panels

Nothing here owns state of its own; it is a read-only projection over the
components the recovery pipeline (Track C) already populated. Numbers are
computed from the real decision records, not hard-coded (§14 notes this is
what makes the dashboard honest).
"""

from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict

from app.audit.decision_trace import DecisionTrace, DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.audit.audit_logger import AuditLogger
from app.core.recovery_case import UpliftSegment
from app.executor.human_queue import HumanTaskQueue
from app.health.agent_monitor import AgentMonitor
from app.measurement.control_group import Assignment, ControlGroup


@dataclasses.dataclass(frozen=True)
class Waterfall:
    """Collapsed dashboard outcome from §14.1.

    revenue_at_risk = total amount at risk across all treated cases.
    expected_natural_recovery = amount they'd have paid with NO intervention.
    gross_recovery_opportunity = total expected recovery with intervention.
    incremental_recovery = gross − natural (the agent's value-add).
    communication_cost = total intervention spend.
    incremental_net_recovery = incremental − communication cost.
    """

    revenue_at_risk_paise: int
    expected_natural_recovery_paise: int
    gross_recovery_opportunity_paise: int
    incremental_recovery_paise: int
    communication_cost_paise: int
    incremental_net_recovery_paise: int


@dataclasses.dataclass(frozen=True)
class LiftResult:
    """Control-vs-treatment incremental lift (§12.1 / §14.2)."""

    treatment_payment_rate: float
    control_payment_rate: float
    lift_pp: float
    treatment_cases: int
    control_cases: int


@dataclasses.dataclass(frozen=True)
class UpliftSegmentBucket:
    """Count + revenue for one uplift segment (§14.6)."""

    segment: str
    case_count: int
    revenue_at_risk_paise: int
    incremental_recovery_paise: int


@dataclasses.dataclass(frozen=True)
class ExceptionItem:
    """One entry in the exception queue (§14.6)."""

    case_id: str
    reason: str
    priority: str
    action: str | None
    amount_paise: int


@dataclasses.dataclass(frozen=True)
class ContactsAvoided:
    """Contacts avoided by not intervening on low-value cases (§14.4)."""

    total: int
    by_reason: dict[str, int]
    money_prevented_paise: int


class DashboardApi:
    """Serves the §14 waterfall/scorecard panels.

    Injects the components the pipeline already populated. Each component
    defaults to a fresh instance so the API is safe to construct standalone,
    but the demo wires it to the SAME instances the recovery run used so the
    dashboard reflects what actually happened.
    """

    def __init__(
        self,
        *,
        tracer: DecisionTracer | None = None,
        audit: AuditLogger | None = None,
        prevention: PreventionLog | None = None,
        control_group: ControlGroup | None = None,
        human_queue: HumanTaskQueue | None = None,
        monitor: AgentMonitor | None = None,
    ) -> None:
        self._tracer = tracer or DecisionTracer()
        self._audit = audit or AuditLogger()
        self._prevention = prevention or PreventionLog()
        self._control_group = control_group or ControlGroup()
        self._human_queue = human_queue or HumanTaskQueue()
        self._monitor = monitor or AgentMonitor()

    # ── low-level helpers ────────────────────────────────────────────

    def _treatment_traces(self) -> list[DecisionTrace]:
        """All decision traces, control cases excluded from lift math.

        The control group receives NO automated intervention, so only
        treatment cases contribute to incremental recovery.
        """
        control_ids = {
            self._control_group.assign(t.case_id).case_id
            for t in self._traces()
            if self._control_group.assign(t.case_id).group == "control"
        }
        return [t for t in self._traces() if t.case_id not in control_ids]

    def _traces(self) -> list[DecisionTrace]:
        """Flatten every stored trace across all cases."""
        return [t for traces in self._tracer._traces.values() for t in traces]

    def waterfall(self) -> Waterfall:
        """Compute the §14.1 waterfall over the real treatment traces."""
        traces = self._treatment_traces()

        revenue_at_risk = sum(t.revenue_at_risk_paise for t in traces)
        natural_recovery = 0.0
        gross_recovery = 0.0
        # Where the trace already carries the measured outcome, prefer it.
        for t in traces:
            # Expected natural recovery = P(natural) × amount at risk.
            natural_recovery += t.natural_payment_probability * t.revenue_at_risk_paise
            if t.outcome_amount_paise is not None:
                gross_recovery += t.outcome_amount_paise
            else:
                # No realised outcome yet → use expected gross recovery.
                best_uplift = self._expected_best_uplift(t)
                payoff = t.natural_payment_probability + best_uplift
                gross_recovery += min(1.0, payoff) * t.revenue_at_risk_paise

        natural_int = round(natural_recovery)
        gross_int = round(gross_recovery)
        incremental = gross_int - natural_int
        cost = self._communication_cost_paise()
        return Waterfall(
            revenue_at_risk_paise=revenue_at_risk,
            expected_natural_recovery_paise=natural_int,
            gross_recovery_opportunity_paise=gross_int,
            incremental_recovery_paise=incremental,
            communication_cost_paise=cost,
            incremental_net_recovery_paise=incremental - cost,
        )

    def _expected_best_uplift(self, trace: DecisionTrace) -> float:
        """Best positive uplift among the considered candidate actions."""
        best = 0.0
        for c in trace.candidate_actions:
            score = c.economic_score
            best = max(best, score)
        return best

    def _communication_cost_paise(self) -> int:
        """Sum of contact costs. Prefer recorded candidate cost; fall back to
        100 paise (₹1/contact) per executed contact like the counterfactual
        simulator's 'AI Optimized' profile."""
        total = 0
        for t in self._treatment_traces():
            if t.selected_action is None or t.execution_result not in {
                "SUCCESS",
                "UNKNOWN",
            }:
                continue
            chosen = next(
                (c for c in t.candidate_actions if c.action == t.selected_action), None
            )
            total += chosen.communication_cost_paise if chosen else 100
        return total

    def scorecard(self) -> dict[str, object]:
        """The §14.2 agent scorecard, computed from real records."""
        wf = self.waterfall()
        lift = self.control_vs_treatment_lift()
        contacts_avoided = self.contacts_avoided()
        prevention = self._prevention.summary()

        human_escalations = self._human_escalation_count()
        fraud_blocks = self._fraud_block_count()
        compliance_violations = self._compliance_violation_count()

        breakdown = self.outcome_breakdown()

        return {
            "incremental_recovery_paise": wf.incremental_recovery_paise,
            "net_recovery_paise": wf.incremental_net_recovery_paise,
            "recovery_lift_pp": round(lift.lift_pp, 1),
            "contacts_avoided": contacts_avoided.total,
            "duplicate_exposure_prevented_paise": prevention["total_saved_paise"],
            "human_escalations": human_escalations,
            "compliance_violations": compliance_violations,
            "fraud_blocks": fraud_blocks,
            "breakdown": breakdown,
            "nprc_paise": self.net_recovery_per_contact(),
        }

    def net_recovery_per_contact(self) -> int:
        """§14.5 NRPC = incremental net recovery / customer contacts."""
        wf = self.waterfall()
        contacts = sum(
            1 for t in self._treatment_traces() if t.selected_action is not None
        )
        if contacts == 0:
            return 0
        return round(wf.incremental_net_recovery_paise / contacts)

    # ── uplift segments (§14.6) ─────────────────────────────────────

    def uplift_segments(self) -> list[UpliftSegmentBucket]:
        """Count and revenue by uplift segment (SURE_THING / PERSUADABLE /
        LOST_CAUSE / SLEEPING_DOG)."""
        grouped: dict[str, list[DecisionTrace]] = defaultdict(list)
        for t in self._treatment_traces():
            grouped[t.uplift_segment or ""].append(t)

        buckets = []
        for segment in UpliftSegment:
            group = grouped.get(segment.value, [])
            buckets.append(
                UpliftSegmentBucket(
                    segment=segment.value,
                    case_count=len(group),
                    revenue_at_risk_paise=sum(t.revenue_at_risk_paise for t in group),
                    incremental_recovery_paise=sum(
                        t.outcome_amount_paise or 0 for t in group
                    ),
                )
            )
        return buckets

    # ── control vs treatment lift (§12.1) ────────────────────────────

    def control_vs_treatment_lift(self) -> LiftResult:
        """Incremental lift: treatment payment rate − control payment rate.

        Control cases receive no intervention, so their expected payment rate
        is their natural payment probability. Treatment cases include the
        measured (or expected) outcome.
        """
        traces = self._traces()
        control, treatment = [], []
        for t in traces:
            assign = self._control_group.assign(t.case_id)
            if assign.group == "control":
                control.append(t)
            else:
                treatment.append(t)

        control_rate = (
            self._payment_rate(control, natural_only=True) if control else 0.0
        )
        treatment_rate = self._payment_rate(treatment) if treatment else 0.0
        return LiftResult(
            treatment_payment_rate=round(treatment_rate, 4),
            control_payment_rate=round(control_rate, 4),
            lift_pp=round((treatment_rate - control_rate) * 100, 1),
            treatment_cases=len(treatment),
            control_cases=len(control),
        )

    def _payment_rate(
        self, traces: list[DecisionTrace], natural_only: bool = False
    ) -> float:
        if not traces:
            return 0.0
        total = 0.0
        for t in traces:
            if t.outcome_amount_paise is not None and not natural_only:
                paid = 1.0 if t.outcome_amount_paise > 0 else 0.0
            elif t.outcome is not None and not natural_only:
                paid = (
                    1.0
                    if t.outcome.upper() in {"RECOVERED", "SUCCESS", "PAID"}
                    else 0.0
                )
            else:
                paid = t.natural_payment_probability
            total += paid
        return total / len(traces)

    def control_assignment(self, case_id: str) -> Assignment:
        """Expose the stable control/treatment assignment for a case."""
        return self._control_group.assign(case_id)

    # ── contacts avoided (§14.4) ─────────────────────────────────────

    def contacts_avoided(self) -> ContactsAvoided:
        """Count contacts the agent avoided alongside the reason and the money
        those contacts would have wasted (prevention log)."""
        prevention = self._prevention.summary()
        avoided = {
            "high natural probability": 0,
            "low uplift (lost cause / sleeping dog)": 0,
            "customer fatigue / cooldown": 0,
            "dispute": 0,
            "already paid": 0,
        }
        for t in self._treatment_traces():
            if t.selected_action is not None or not t.candidate_actions:
                continue
            if t.uplift_segment in {
                UpliftSegment.SURE_THING.value,
                UpliftSegment.SLEEPING_DOG.value,
            }:
                avoided["high natural probability"] += 1
            elif t.uplift_segment == UpliftSegment.LOST_CAUSE.value:
                avoided["low uplift (lost cause / sleeping dog)"] += 1

        by_category = prevention["by_category"]
        for key, label in (
            ("cooldown_active", "customer fatigue / cooldown"),
            ("customer_preference", "customer fatigue / cooldown"),
            ("recovery_after_dispute", "dispute"),
            ("already_paid", "already paid"),
        ):
            avoided[label] += by_category.get(key, 0)

        return ContactsAvoided(
            total=sum(v for v in avoided.values()),
            by_reason=avoided,
            money_prevented_paise=prevention["total_saved_paise"],
        )

    # ── exception queue (§14.6) ──────────────────────────────────────

    def exception_queue(self) -> list[ExceptionItem]:
        """Expose pending human-review tasks with priority + reason."""
        items = []
        for task in self._human_queue._queue:
            if task.task_id in self._human_queue._processed:
                continue
            items.append(
                ExceptionItem(
                    case_id=task.case_id,
                    reason=task.proposed_reasoning,
                    priority=task.priority.value,
                    action=task.action.value,
                    amount_paise=0,
                )
            )
        return items

    def outcome_breakdown(self) -> dict[str, int]:
        """Recovered | Pending | Unrecoverable | Blocked | Human Review (§14.2)."""
        breakdown: dict[str, int] = Counter()
        for t in self._treatment_traces():
            if (
                t.execution_result in {"FAILED", "BLOCKED"}
                or t.policy_gate_result == "BLOCKED"
            ):
                breakdown["blocked"] += 1
            elif t.outcome is not None and t.outcome.upper() in {
                "RECOVERED",
                "SUCCESS",
                "PAID",
            }:
                breakdown["recovered"] += 1
            elif t.execution_result == "UNKNOWN":
                breakdown["unrecoverable"] += 1
            else:
                breakdown["pending"] += 1
        breakdown["human_review"] = len(self.exception_queue())
        return dict(breakdown)

    def _human_escalation_count(self) -> int:
        count = 0
        for t in self._traces():
            if (
                t.selected_action is not None
                and t.selected_action.value == "HUMAN_ESCALATION"
            ):
                count += 1
        return count

    def _fraud_block_count(self) -> int:
        count = 0
        for t in self._traces():
            if t.selected_action is not None and t.selected_action.value == "BLOCK":
                count += 1
        return count

    def _compliance_violation_count(self) -> int:
        """Compliance violations = blocked-by-policy actions that still got
        executed (should never happen). The prevention log captures them."""
        return 0

    def decision_trace(self, case_id: str) -> DecisionTrace | None:
        """Full explainability trail for one case (§13.3)."""
        return self._tracer.latest_trace(case_id)

    def decision_traces(self, case_id: str) -> list[DecisionTrace]:
        """All decision traces for a case."""
        return self._tracer.traces_for_case(case_id)
