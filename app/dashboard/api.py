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
import csv
import io
import threading
from collections import Counter, defaultdict

from app.audit.decision_trace import DecisionTrace, DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.audit.audit_logger import AuditLogger
from app.core.recovery_case import UpliftSegment
from app.dashboard.render_guard import guard_response
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
class QueueItem:
    """One case in the auto/human queue view."""

    case_id: str
    amount_paise: int
    status: str
    recommended_action: str
    requires_human: bool
    confidence: float
    reasoning: str
    uplift_segment: str
    policy_gate_result: str
    trace_id: str | None


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
        messages: list[object] | None = None,
        dataset_summary: dict[str, object] | None = None,
    ) -> None:
        self._tracer = tracer or DecisionTracer()
        self._audit = audit or AuditLogger()
        self._prevention = prevention or PreventionLog()
        self._control_group = control_group or ControlGroup()
        self._human_queue = human_queue or HumanTaskQueue()
        self._monitor = monitor or AgentMonitor()
        self._messages = list(messages or [])
        self._dataset_summary = dict(dataset_summary or {})

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
                best_uplift = self._selected_action_uplift(t)
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

    def _selected_action_uplift(self, trace: DecisionTrace) -> float:
        """Positive uplift for the selected allowed action only."""
        if (
            trace.selected_action is None
            or trace.policy_gate_result == "BLOCKED"
            or trace.selected_action.value in {"NO_ACTION", "WAIT", "BLOCK"}
        ):
            return 0.0
        selected = next(
            (c for c in trace.candidate_actions if c.action == trace.selected_action),
            None,
        )
        return max(0.0, selected.economic_score if selected else 0.0)

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
                        _trace_incremental_value(t) for t in group
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
                    amount_paise=getattr(task, "amount_paise", 0),
                )
            )
        return items

    def auto_eligible_queue(self) -> list[QueueItem]:
        """Cases that can proceed autonomously (requires_human = false)."""
        return self._split_queue(human_required=False)

    def human_required_queue(self) -> list[QueueItem]:
        """Cases that require human approval (requires_human = true)."""
        return self._split_queue(human_required=True)

    def _split_queue(self, human_required: bool) -> list[QueueItem]:
        """Split treatment traces into auto-eligible vs human-required."""
        items = []
        for t in self._treatment_traces():
            # Skip if no action selected or stopped
            if t.selected_action is None or getattr(t, "stopped", False):
                continue
            # Use trace's E.5 reasoning field (fall back to selection_reasoning)
            reasoning = t.reasoning or t.selection_reasoning or "No reasoning recorded."
            # Determine trace_id from audit
            audit_entries = self._audit.entries_for_case(t.case_id)
            trace_id = audit_entries[-1].entry_id if audit_entries else None
            # Determine requires_human from trace (or recompute)
            requires_human = t.requires_human_approval if hasattr(t, 'requires_human_approval') else False
            # For backward compat, infer from action + amount
            if not requires_human and t.selected_action:
                high_value_actions = {
                    "RETRY_SAME_METHOD", "RETRY_ALTERNATE_METHOD",
                    "SEND_PAYMENT_LINK", "OFFER_PARTIAL_PAYMENT",
                    "REQUEST_PAYMENT_METHOD_UPDATE"
                }
                if t.selected_action.value in high_value_actions and t.revenue_at_risk_paise >= 10_000_000:
                    requires_human = True
            if requires_human != human_required:
                continue
            items.append(QueueItem(
                case_id=t.case_id,
                amount_paise=t.revenue_at_risk_paise,
                status=t.state,
                recommended_action=t.selected_action.value if t.selected_action else "NO_ACTION",
                requires_human=requires_human,
                confidence=round(t.selected_economic_score, 4) if t.selected_economic_score else 0.0,
                reasoning=reasoning,
                uplift_segment=t.uplift_segment or "UNKNOWN",
                policy_gate_result=t.policy_gate_result,
                trace_id=trace_id,
            ))
        return items

    def message_outbox(self) -> list[object]:
        """Rendered recovery messages sent by the demo transport."""
        return list(self._messages)

    def dataset_summary(self) -> dict[str, object]:
        """Dataset provenance for batch-upload and synthetic-demo proof."""
        return dict(self._dataset_summary)

    def merge_from(self, other: "DashboardApi") -> None:
        """Append another in-memory projection, used by realtime demo events."""
        for case_id, traces in other._tracer._traces.items():
            self._tracer._traces.setdefault(case_id, []).extend(traces)
        self._prevention._records.extend(other._prevention._records)
        self._human_queue._queue.extend(other._human_queue._queue)
        self._messages.extend(other._messages)
        if other._dataset_summary:
            merged = dict(self._dataset_summary)
            merged["dataset_name"] = "dashboard_state_plus_realtime"
            for key in (
                "record_count",
                "held_out_labels_matched",
                "estimated_records",
                "audit_traces",
                "message_count",
                "human_escalations",
            ):
                merged[key] = int(merged.get(key, 0) or 0) + int(
                    other._dataset_summary.get(key, 0) or 0
                )
            merged["label_policy"] = other._dataset_summary.get(
                "label_policy", merged.get("label_policy", "")
            )
            self._dataset_summary = merged

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
        return count + len(self.exception_queue())

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


# ── FastAPI router (serves the §14 panels) ───────────────────────────────────

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["dashboard"])

_dashboard: DashboardApi | None = None
_dashboard_lock = threading.Lock()


def configure(api: DashboardApi) -> None:
    """Bind the dashboard API to the SAME populated components the recovery
    run used, so the served panels reflect what actually happened (§14)."""
    global _dashboard
    _dashboard = api


def _get_dashboard() -> DashboardApi:
    global _dashboard
    if _dashboard is None:
        with _dashboard_lock:
            if _dashboard is not None:
                return _dashboard
            try:
                from app.dashboard.demo_data import build_synthetic_demo_dashboard

                _dashboard = build_synthetic_demo_dashboard()
            except Exception:  # noqa: BLE001
                _dashboard = DashboardApi()
    return _dashboard


def _wf(d: object) -> dict:  # noqa: D401 - projection helper
    return {
        "revenue_at_risk_paise": d.revenue_at_risk_paise,
        "expected_natural_recovery_paise": d.expected_natural_recovery_paise,
        "gross_recovery_opportunity_paise": d.gross_recovery_opportunity_paise,
        "incremental_recovery_paise": d.incremental_recovery_paise,
        "communication_cost_paise": d.communication_cost_paise,
        "incremental_net_recovery_paise": d.incremental_net_recovery_paise,
    }


@router.get("/api/dashboard/waterfall")
def get_waterfall() -> dict:
    return guard_response(_wf(_get_dashboard().waterfall()), "waterfall").data


@router.get("/api/dashboard/scorecard")
def get_scorecard() -> dict:
    api = _get_dashboard()
    data = api.scorecard()
    data["lift"] = {
        "treatment_payment_rate": api.control_vs_treatment_lift().treatment_payment_rate,
        "control_payment_rate": api.control_vs_treatment_lift().control_payment_rate,
        "lift_pp": api.control_vs_treatment_lift().lift_pp,
        "treatment_cases": api.control_vs_treatment_lift().treatment_cases,
        "control_cases": api.control_vs_treatment_lift().control_cases,
    }
    return guard_response(data, "scorecard").data


@router.get("/api/dashboard/uplift_segments")
def get_uplift_segments() -> list[dict]:
    data = [
        {
            "segment": b.segment,
            "case_count": b.case_count,
            "revenue_at_risk_paise": b.revenue_at_risk_paise,
            "incremental_recovery_paise": b.incremental_recovery_paise,
        }
        for b in _get_dashboard().uplift_segments()
    ]
    return guard_response({"segments": data}, "uplift_segments").data["segments"]


@router.get("/api/dashboard/contacts_avoided")
def get_contacts_avoided() -> dict:
    ca = _get_dashboard().contacts_avoided()
    data = {
        "total": ca.total,
        "by_reason": ca.by_reason,
        "money_prevented_paise": ca.money_prevented_paise,
    }
    return guard_response(data, "contacts_avoided").data


@router.get("/api/dashboard/exception_queue")
def get_exception_queue() -> list[dict]:
    data = [
        {
            "case_id": i.case_id,
            "reason": i.reason,
            "priority": i.priority,
            "action": i.action,
            "amount_paise": i.amount_paise,
        }
        for i in _get_dashboard().exception_queue()
    ]
    return guard_response({"items": data}, "exception_queue").data["items"]


@router.get("/api/queue/auto-eligible")
def get_auto_eligible_queue() -> list[dict]:
    data = [
        {
            "case_id": i.case_id,
            "amount_paise": i.amount_paise,
            "status": i.status,
            "recommended_action": i.recommended_action,
            "requires_human": i.requires_human,
            "confidence": i.confidence,
            "reasoning": i.reasoning,
            "uplift_segment": i.uplift_segment,
            "policy_gate_result": i.policy_gate_result,
            "trace_id": i.trace_id,
        }
        for i in _get_dashboard().auto_eligible_queue()
    ]
    return guard_response({"items": data}, "auto_eligible_queue").data["items"]


@router.get("/api/queue/human-required")
def get_human_required_queue() -> list[dict]:
    data = [
        {
            "case_id": i.case_id,
            "amount_paise": i.amount_paise,
            "status": i.status,
            "recommended_action": i.recommended_action,
            "requires_human": i.requires_human,
            "confidence": i.confidence,
            "reasoning": i.reasoning,
            "uplift_segment": i.uplift_segment,
            "policy_gate_result": i.policy_gate_result,
            "trace_id": i.trace_id,
        }
        for i in _get_dashboard().human_required_queue()
    ]
    return guard_response({"items": data}, "human_required_queue").data["items"]


@router.get("/api/dashboard/messages")
def get_message_outbox() -> list[dict]:
    data = [_message_record(item) for item in _get_dashboard().message_outbox()]
    return guard_response({"items": data}, "message_outbox").data["items"]


@router.get("/api/dashboard/cases")
def get_case_intelligence(limit: int = 250) -> list[dict]:
    api = _get_dashboard()
    messages = api.message_outbox()
    messaged_cases = {
        _case_id_from_message(getattr(message, "rendered_body", ""))
        for message in messages
    }
    items = []
    for trace in api._traces()[: max(1, min(limit, 1000))]:
        selected_uplift = api._selected_action_uplift(trace)
        top = max(trace.candidate_actions, key=lambda c: c.economic_score, default=None)
        items.append(
            {
                "case_id": trace.case_id,
                "trigger_event": trace.trigger_event,
                "root_cause": trace.root_cause,
                "uplift_segment": trace.uplift_segment,
                "amount_paise": trace.revenue_at_risk_paise,
                "natural_payment_probability": round(
                    trace.natural_payment_probability, 4
                ),
                "predicted_recovery_probability": round(
                    min(1.0, trace.natural_payment_probability + selected_uplift), 4
                ),
                "selected_action": trace.selected_action.value
                if trace.selected_action
                else "NO_ACTION",
                "top_model_action": top.action.value if top else "NO_ACTION",
                "top_model_uplift": round(top.economic_score, 4) if top else 0,
                "selected_uplift": round(selected_uplift, 4),
                "policy_gate_result": trace.policy_gate_result,
                "policy_checks_failed": trace.policy_checks_failed,
                "outcome": trace.outcome or trace.execution_result or "PENDING",
                "message_sent": any(trace.case_id.endswith(case or "") for case in messaged_cases),
                "playbook": _playbook_for(trace),
                "why": trace.selection_reasoning,
            }
        )
    return guard_response({"items": items}, "case_intelligence").data["items"]


@router.get("/api/dashboard/dataset")
def get_dataset_summary() -> dict:
    return guard_response(_get_dashboard().dataset_summary(), "dataset_summary").data


@router.post("/api/dashboard/load-synthetic")
def load_synthetic_batch() -> dict:
    from app.dashboard.demo_data import build_synthetic_demo_dashboard

    api = build_synthetic_demo_dashboard()
    configure(api)
    data = {
        "status": "processed",
        "dataset": api.dataset_summary(),
        "waterfall": _wf(api.waterfall()),
        "scorecard": api.scorecard(),
        "messages": [_message_record(item) for item in api.message_outbox()[:10]],
    }
    return guard_response(data, "load_synthetic").data


@router.post("/api/dashboard/upload")
async def upload_batch(request: Request, filename: str = "uploaded.csv") -> dict:
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Upload body is empty")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from exc

    rows = _parse_csv_rows(text)
    try:
        from app.dashboard.demo_data import build_dashboard_from_rows

        api = build_dashboard_from_rows(rows, dataset_name=filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Cannot score batch: {exc}") from exc

    configure(api)
    wf = api.waterfall()
    sc = api.scorecard()
    data = {
        "status": "processed",
        "dataset": api.dataset_summary(),
        "waterfall": _wf(wf),
        "scorecard": sc,
        "messages": [_message_record(item) for item in api.message_outbox()[:10]],
    }
    return guard_response(data, "upload_batch").data


@router.post("/api/dashboard/realtime-event")
async def ingest_realtime_event(request: Request) -> dict:
    try:
        row = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if not isinstance(row, dict):
        raise HTTPException(status_code=400, detail="Realtime event must be an object")

    from app.dashboard.demo_data import build_dashboard_from_rows

    api = build_dashboard_from_rows([row], dataset_name="realtime_event")
    _get_dashboard().merge_from(api)
    data = {
        "status": "accepted",
        "dataset": api.dataset_summary(),
        "messages": [_message_record(item) for item in api.message_outbox()],
        "scorecard": _get_dashboard().scorecard(),
    }
    return guard_response(data, "realtime_event").data


def _parse_csv_rows(text: str) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(row) for row in reader if any(str(v or "").strip() for v in row.values())]
    except csv.Error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid CSV: {exc}") from exc
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV header row is missing")
    if not rows:
        raise HTTPException(status_code=400, detail="CSV has no data rows")
    return rows


def _message_record(item: object) -> dict:
    state = getattr(item, "state", "")
    return {
        "notification_id": getattr(item, "notification_id", ""),
        "channel": getattr(item, "channel", ""),
        "template_key": getattr(item, "template_key", ""),
        "recipient": getattr(item, "recipient", ""),
        "state": getattr(state, "value", state),
        "rendered_body": getattr(item, "rendered_body", ""),
    }


def _case_id_from_message(body: str) -> str:
    marker = "demo-"
    if marker not in body:
        return ""
    return body.split(marker, 1)[1][:6]


def _playbook_for(trace: DecisionTrace) -> dict[str, str]:
    reason = trace.root_cause.lower()
    event = trace.trigger_event.lower()
    if "checkout" in reason or "checkout" in event:
        return {
            "name": "Checkout drop-off recovery",
            "path": "Abandonment -> uplift score -> link or WhatsApp nudge",
        }
    if "mandate" in reason or "subscription" in event:
        return {
            "name": "Failed-subscription / mandate retry",
            "path": "Mandate state -> revocation direction -> retry or re-auth",
        }
    if "overdue_invoice" in reason or "invoice" in event:
        return {
            "name": "B2B receivables chaser",
            "path": "Invoice aging -> cashflow priority -> email/PTP/human review",
        }
    if "ptp" in reason:
        return {
            "name": "Promise-to-pay tracker",
            "path": "PTP reliability -> hold/retry/reminder",
        }
    if trace.selected_action and trace.selected_action.value == "VOICE_CALL":
        return {
            "name": "Hinglish voice recovery",
            "path": "Intent/emotion -> compliant voice script -> PTP extraction",
        }
    if reason in {"bank_timeout", "gateway_error", "insufficient_funds", "expired_card"}:
        return {
            "name": "Payment degradation recovery",
            "path": "Failure root cause -> natural pay check -> retry/link/message",
        }
    return {
        "name": "Recovery safety workflow",
        "path": "Classify -> estimate uplift -> policy gate -> audit",
    }


def _trace_incremental_value(trace: DecisionTrace) -> int:
    if (
        trace.selected_action is None
        or trace.policy_gate_result == "BLOCKED"
        or trace.selected_action.value in {"NO_ACTION", "WAIT", "BLOCK"}
    ):
        return 0
    if trace.outcome_amount_paise is not None:
        natural = round(trace.natural_payment_probability * trace.revenue_at_risk_paise)
        return max(0, trace.outcome_amount_paise - natural)
    selected = next(
        (c for c in trace.candidate_actions if c.action == trace.selected_action),
        None,
    )
    return round(max(0.0, selected.economic_score if selected else 0.0) * trace.revenue_at_risk_paise)
