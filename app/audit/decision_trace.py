"""Decision trace — full structured decision record (§13.3, §13.4, §1.2).

Per §1.2, every decision must capture:
  - What happened (the trigger event)
  - Why this intervention (economic score, uplift)
  - Why now (timing rationale)
  - What was rejected and why (§13.4)
  - What policy checks passed / failed (with per-gate detail + legal basis)
  - Was recovery incremental (control group comparison)

Judge can click "WHY DID YOU SEND THIS?" and see everything.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime

from app.contracts import Action, CandidateAction
from app.core.clock import clock

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PolicyGateDetail:
    """Per-gate policy evaluation detail with legal basis (§7)."""

    gate_name: str
    result: str  # "PASS" | "FAIL" | "BLOCKED" | "SKIPPED"
    reason: str | None
    legal_basis: str
    legal_basis_description: str


@dataclasses.dataclass(frozen=True)
class DecisionTrace:
    """The complete structured trail for one case decision (§13.3, §13.4).

    Judges click 'WHY DID YOU SEND THIS?' and see every step.
    """

    # §1.2: What happened
    case_id: str
    trigger_event: str
    trigger_timestamp: datetime

    # §1.2: Current state
    state: str
    root_cause: str
    natural_payment_probability: float
    uplift_segment: str
    revenue_at_risk_paise: int

    # §1.2: Why this intervention
    candidate_actions: list[CandidateAction]
    selected_action: Action | None
    selected_economic_score: float
    selection_reasoning: str

    # §1.2: Why now
    timing_rationale: str

    # §1.2: What was rejected and why (§13.4)
    rejected_actions: dict[str, str]  # action_value → reason

    # §1.2: What policy checks passed/failed
    policy_checks_passed: list[str]
    policy_checks_failed: list[str]
    policy_gate_result: str  # APPROVED / BLOCKED

    # §1.2 + §7: Detailed per-gate policy evaluation with legal basis
    policy_gate_details: list[PolicyGateDetail] = dataclasses.field(default_factory=list)

    # §E.4: LLM-generated diagnostic rationale (generated once after classification)
    diagnostic_rationale: str = ""

    # Execution
    execution_result: str | None = None
    execution_detail: str | None = None
    idempotency_key: str | None = None

    # §1.2: Was recovery incremental
    is_incremental: bool = False
    control_group: bool = False
    outcome: str | None = None
    outcome_amount_paise: int | None = None

    # §E.5: Human approval flag and action reasoning
    requires_human_approval: bool = False
    reasoning: str = ""

    # §1.2: one-paragraph "why this case is here" (E.13) / E.10 narrative
    case_narrative: str = ""

    # Versions
    model_versions: dict = dataclasses.field(default_factory=dict)


class DecisionTracer:
    """Builds and stores the DecisionTrace for every case (§13.3).

    This is the "explainability engine" — the judge demo depends on it.
    """

    def __init__(self) -> None:
        self._traces: dict[str, list[DecisionTrace]] = {}

    def build(
        self,
        *,
        case_id: str,
        trigger_event: str,
        state: str,
        root_cause: str,
        natural_payment_probability: float,
        uplift_segment: str,
        revenue_at_risk_paise: int,
        candidate_actions: list[CandidateAction],
        selected_action: Action | None,
        selected_economic_score: float = 0.0,
        selection_reasoning: str = "",
        timing_rationale: str = "",
        rejected_actions: dict[str, str] | None = None,
        policy_checks_passed: list[str] | None = None,
        policy_checks_failed: list[str] | None = None,
        policy_gate_result: str = "APPROVED",
        policy_gate_details: list[PolicyGateDetail] | None = None,
        diagnostic_rationale: str = "",
        execution_result: str | None = None,
        execution_detail: str | None = None,
        idempotency_key: str | None = None,
        is_incremental: bool = False,
        control_group: bool = False,
        outcome: str | None = None,
        outcome_amount_paise: int | None = None,
        model_versions: dict | None = None,
        requires_human_approval: bool = False,
        reasoning: str = "",
        case_narrative: str = "",
    ) -> DecisionTrace:
        """Build a complete decision trace and store it."""
        trace = DecisionTrace(
            case_id=case_id,
            trigger_event=trigger_event,
            trigger_timestamp=clock.now(),
            state=state,
            root_cause=root_cause,
            natural_payment_probability=natural_payment_probability,
            uplift_segment=uplift_segment,
            revenue_at_risk_paise=revenue_at_risk_paise,
            candidate_actions=candidate_actions,
            selected_action=selected_action,
            selected_economic_score=selected_economic_score,
            selection_reasoning=selection_reasoning,
            timing_rationale=timing_rationale,
            rejected_actions=rejected_actions or {},
            policy_checks_passed=policy_checks_passed or [],
            policy_checks_failed=policy_checks_failed or [],
            policy_gate_result=policy_gate_result,
            policy_gate_details=policy_gate_details or [],
            diagnostic_rationale=diagnostic_rationale,
            execution_result=execution_result,
            execution_detail=execution_detail,
            idempotency_key=idempotency_key,
            is_incremental=is_incremental,
            control_group=control_group,
            outcome=outcome,
            outcome_amount_paise=outcome_amount_paise,
            model_versions=model_versions or {},
            requires_human_approval=requires_human_approval,
            reasoning=reasoning,
            case_narrative=case_narrative,
        )

        self._traces.setdefault(case_id, []).append(trace)

        logger.info(
            "Decision trace for case %s: %s → %s (score=%.2f, policy=%s)",
            case_id,
            trigger_event,
            selected_action.value if selected_action else "NONE",
            selected_economic_score,
            policy_gate_result,
        )
        return trace

    def why_not(
        self,
        case_id: str,
        chosen: Action,
        candidates: list[CandidateAction],
        policy_results: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the "Why Not?" explanation (§13.4).

        For every considered action that was NOT chosen, explain why:
          SMS   → Lower expected uplift (+9% vs +37%)
          Voice → Outside preferred contact period
          Retry → Bank downtime active
          No Action → Expected natural payment too low (22%)
        """
        reasons: dict[str, str] = {}
        policy = policy_results or {}

        chosen_score = 0.0
        for c in candidates:
            if c.action == chosen:
                chosen_score = c.economic_score
                break

        for candidate in candidates:
            if candidate.action == chosen:
                continue

            action_key = candidate.action.value

            # Policy blocked?
            if action_key in policy:
                reasons[action_key] = f"Policy blocked: {policy[action_key]}"
                continue

            # Lower score?
            if candidate.economic_score < chosen_score:
                reasons[action_key] = (
                    f"Lower economic score ({candidate.economic_score:.2f} "
                    f"vs {chosen_score:.2f} for {chosen.value})"
                )
            elif candidate.economic_score == chosen_score:
                reasons[action_key] = (
                    f"Equal score but {chosen.value} preferred by default ordering"
                )
            else:
                reasons[action_key] = "Unknown reason"

        return reasons

    def traces_for_case(self, case_id: str) -> list[DecisionTrace]:
        """Get all decision traces for a case."""
        return list(self._traces.get(case_id, []))

    def latest_trace(self, case_id: str) -> DecisionTrace | None:
        """Get the most recent decision trace for a case."""
        traces = self._traces.get(case_id, [])
        return traces[-1] if traces else None
