"""Base recovery module — the AI Loop from §1.3.

Every recovery module inherits this base and follows the EXACT same pipeline:

  OBSERVE → RECONSTRUCT FINANCIAL STATE → DIAGNOSE → PREDICT NATURAL PAYMENT
  → ESTIMATE INTERVENTION UPLIFT → GENERATE CANDIDATES → OPTIMIZE ECONOMIC VALUE
  → APPLY HARD POLICY → EXECUTE BOUNDED ACTION → RECONCILE ACTUAL PAYMENT STATE
  → MEASURE INCREMENTAL RECOVERY → LEARN

NOT: Webhook → LLM → SMS

The three-layer separation (§2.2) is enforced here:
  AI/ML Layer → PROPOSES
  Policy Engine → GATES
  Executor → PERFORMS
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.audit.version_tracker import VersionTracker
from app.contracts import Action, CandidateAction, PolicyGateResult
from app.core.recovery_case import RecoveryCase
from app.executor.action_executor import ActionExecutor, ExecutionResult
from app.optimizer.intervention_optimizer import InterventionOptimizer
from app.policy.policy_engine import PolicyEngine
from app.reconciliation.pre_action_check import PreActionReconciler
from app.revenue_risk.uplift_model import UpliftEstimates

logger = logging.getLogger(__name__)


class RecoveryModuleResult:
    """Outcome of running one recovery module step."""

    def __init__(
        self,
        case_id: str,
        module_name: str,
        selected_action: Action | None = None,
        execution_result: ExecutionResult | None = None,
        policy_blocked: bool = False,
        reconciliation_cancelled: bool = False,
        reason: str = "",
    ):
        self.case_id = case_id
        self.module_name = module_name
        self.selected_action = selected_action
        self.execution_result = execution_result
        self.policy_blocked = policy_blocked
        self.reconciliation_cancelled = reconciliation_cancelled
        self.reason = reason

    @property
    def succeeded(self) -> bool:
        return (
            self.execution_result is not None
            and self.execution_result.state.value == "SUCCESS"
        )


class BaseRecoveryModule(ABC):
    """Abstract base that enforces the AI Loop (§1.3) for every module.

    Subclasses MUST implement:
      - module_name: str property
      - diagnose(): root-cause classification
      - generate_candidates(): domain-specific action candidates
      - build_payload(): execution payload for the selected action

    The base class handles the pipeline orchestration, ensuring the
    three-layer separation is never broken.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        executor: ActionExecutor,
        optimizer: InterventionOptimizer | None = None,
        pre_action: PreActionReconciler | None = None,
        audit: AuditLogger | None = None,
        tracer: DecisionTracer | None = None,
        prevention_log: PreventionLog | None = None,
        version_tracker: VersionTracker | None = None,
    ) -> None:
        self._policy = policy_engine
        self._executor = executor
        self._optimizer = optimizer or InterventionOptimizer()
        self._pre_action = pre_action or PreActionReconciler()
        self._audit = audit or AuditLogger()
        self._tracer = tracer or DecisionTracer()
        self._prevention = prevention_log or PreventionLog()
        self._versions = version_tracker or VersionTracker()

    @property
    @abstractmethod
    def module_name(self) -> str:
        """Unique name for this recovery module."""

    @abstractmethod
    def diagnose(self, case: RecoveryCase) -> str:
        """Step 3: DIAGNOSE — classify root cause."""

    @abstractmethod
    def generate_candidates(self, case: RecoveryCase) -> list[CandidateAction]:
        """Step 6: GENERATE CANDIDATES — domain-specific action set."""

    def build_payload(self, case: RecoveryCase, action: Action) -> dict:
        """Build execution payload for the selected action.
        Override in subclasses for action-specific payloads.
        """
        return {
            "case_id": case.case_id,
            "customer_id": case.customer_id,
            "module": self.module_name,
            "amount_paise": case.total_remaining(),
        }

    def _build_uplift_estimates(
        self, case: RecoveryCase, candidates: list[CandidateAction]
    ) -> UpliftEstimates:
        """Project the module's candidates onto an UpliftEstimates.

        The optimizer consumes per-action probability uplift.  We derive a
        probability-style uplift from each candidate's net economic value
        (paise) normalised by the remaining balance, so the optimizer can
        rank and price the actions it re-builds.
        """
        baseline = case.natural_pay_probability
        amount = case.total_remaining() or 1
        per_action_uplift: dict[Action, float] = {}
        for candidate in candidates:
            ratio = max(
                -0.5,
                min(0.5, candidate.economic_score / amount),
            )
            per_action_uplift[candidate.action] = ratio
        per_action_probability = {
            action: max(0.0, min(1.0, baseline + uplift))
            for action, uplift in per_action_uplift.items()
        }
        return UpliftEstimates(
            baseline_natural_probability=round(baseline, 4),
            per_action_probability=per_action_probability,
            per_action_uplift=per_action_uplift,
            uplift_segment=case.uplift_segment,
        )

    # ── The AI Loop (§1.3) ────────────────────────────────────────

    def run(self, case: RecoveryCase) -> RecoveryModuleResult:
        """Execute the full AI loop for this case.

        OBSERVE → RECONSTRUCT → DIAGNOSE → PREDICT → ESTIMATE UPLIFT
        → GENERATE CANDIDATES → OPTIMIZE → POLICY → EXECUTE → RECONCILE
        → MEASURE → LEARN
        """
        logger.info(
            "═══ %s: Running AI loop for case %s ═══",
            self.module_name,
            case.case_id,
        )

        # ── Step 1: OBSERVE ───────────────────────────────────────
        # The trigger event has already been received and the case exists.
        logger.debug("[%s] Step 1: OBSERVE — case %s", self.module_name, case.case_id)

        # ── Step 2: RECONSTRUCT FINANCIAL STATE ───────────────────
        remaining = case.total_remaining()
        if remaining <= 0:
            logger.info(
                "[%s] Case %s already recovered (remaining=0). Skipping.",
                self.module_name,
                case.case_id,
            )
            return RecoveryModuleResult(
                case_id=case.case_id,
                module_name=self.module_name,
                reason="already recovered",
            )

        # ── Step 3: DIAGNOSE ──────────────────────────────────────
        root_cause = self.diagnose(case)
        logger.debug(
            "[%s] Step 3: DIAGNOSE — root_cause=%s", self.module_name, root_cause
        )

        # ── Step 4: PREDICT NATURAL PAYMENT ───────────────────────
        natural_prob = case.natural_pay_probability
        logger.debug(
            "[%s] Step 4: PREDICT — natural_pay_prob=%.2f",
            self.module_name,
            natural_prob,
        )

        # ── Step 6: GENERATE CANDIDATES ───────────────────────────
        candidates = self.generate_candidates(case)
        if not candidates:
            logger.info(
                "[%s] No candidate actions for case %s.",
                self.module_name,
                case.case_id,
            )
            return RecoveryModuleResult(
                case_id=case.case_id,
                module_name=self.module_name,
                selected_action=Action.NO_ACTION,
                reason="no candidates generated",
            )

        # ── Step 7: OPTIMIZE ECONOMIC VALUE ───────────────────────
        # Use the intervention optimizer to select the best action.  The
        # optimizer applies the confidence ladder, fatigue/stopping rules, and
        # computes requires_human_approval + reasoning (E.5).  The module's
        # candidates are projected onto the UpliftEstimates the optimizer needs.
        estimates = self._build_uplift_estimates(case, candidates)
        recommendation = self._optimizer.optimize(case, estimates)
        
        logger.debug(
            "[%s] Step 7: OPTIMIZE — selected %s (score=%.2f, human=%s)",
            self.module_name,
            recommendation.selected_action.value if recommendation.selected_action else "NONE",
            recommendation.economic_scores.get(recommendation.selected_action, 0.0),
            recommendation.requires_human_approval,
        )

        # Use the optimizer's selected action and rejected reasons
        selected_action: Action | None = recommendation.selected_action
        selected = next(
            (c for c in candidates if c.action == selected_action), None
        )
        rejected_reasons = recommendation.rejected_reasons
        policy_passed = []
        policy_failed = []
        
        if selected is not None:
            # Check policy for the selected action
            evaluation = self._policy.evaluate(case, selected_action)
            if evaluation.result == PolicyGateResult.APPROVED:
                policy_passed = list(evaluation.passed_checks)
            else:
                rejected_value = selected_action.value
                policy_failed = list(evaluation.failed_checks)
                rejected_reasons[rejected_value] = "; ".join(
                    evaluation.blocked_reasons
                )
                # Log prevention
                self._prevention.log(
                    case_id=case.case_id,
                    prevented_action=rejected_value,
                    reason="; ".join(evaluation.blocked_reasons),
                    amount_saved_paise=case.total_remaining(),
                )
                selected = None

        if selected is None:
            logger.warning(
                "[%s] All candidates blocked by policy for case %s.",
                self.module_name,
                case.case_id,
            )
            # Build decision trace even for blocked decisions
            self._tracer.build(
                case_id=case.case_id,
                trigger_event=self.module_name,
                state=case.state.value,
                root_cause=root_cause,
                natural_payment_probability=natural_prob,
                uplift_segment=str(case.uplift_segment),
                revenue_at_risk_paise=remaining,
                candidate_actions=candidates,
                selected_action=None,
                rejected_actions=rejected_reasons,
                policy_checks_passed=policy_passed,
                policy_checks_failed=list(set(policy_failed)),
                policy_gate_result="BLOCKED",
                model_versions=self._versions.current().__dict__,
                requires_human_approval=False,
                reasoning="All candidates blocked by policy — no automated action permitted.",
            )
            return RecoveryModuleResult(
                case_id=case.case_id,
                module_name=self.module_name,
                policy_blocked=True,
                reason="all candidates blocked by policy",
            )

        logger.info(
            "[%s] Step 8: POLICY APPROVED — %s for case %s",
            self.module_name,
            selected.action.value,
            case.case_id,
        )

        # ── Step 8b: PRE-ACTION RECONCILIATION (§8.2) ─────────────
        pre_check = self._pre_action.check(case, selected.action)
        if not pre_check.allow:
            logger.info(
                "[%s] Pre-action reconciliation CANCELLED %s: %s",
                self.module_name,
                selected.action.value,
                pre_check.reason,
            )
            self._prevention.log(
                case_id=case.case_id,
                prevented_action=selected.action.value,
                reason=f"pre-action reconciliation: {pre_check.reason}",
                amount_saved_paise=case.total_remaining(),
            )
            return RecoveryModuleResult(
                case_id=case.case_id,
                module_name=self.module_name,
                selected_action=selected.action,
                reconciliation_cancelled=True,
                reason=f"pre-action: {pre_check.reason}",
            )

        # ── Step 9: EXECUTE BOUNDED ACTION ────────────────────────
        payload = self.build_payload(case, selected.action)
        exec_result = self._executor.execute(case, selected.action, payload)

        logger.info(
            "[%s] Step 9: EXECUTE — %s → %s",
            self.module_name,
            selected.action.value,
            exec_result.state.value,
        )

        # ── Step 10: RECONCILE ACTUAL PAYMENT STATE ───────────────
        # (Handled by the reconciliation engine asynchronously)

        # ── Step 11: MEASURE INCREMENTAL RECOVERY ─────────────────
        # (Handled by the experiment engine)

        # ── Step 12: LEARN ────────────────────────────────────────
        # Build and store the complete decision trace
        self._tracer.build(
            case_id=case.case_id,
            trigger_event=self.module_name,
            state=case.state.value,
            root_cause=root_cause,
            natural_payment_probability=natural_prob,
            uplift_segment=str(case.uplift_segment),
            revenue_at_risk_paise=remaining,
            candidate_actions=candidates,
            selected_action=selected.action,
            selected_economic_score=recommendation.economic_scores.get(
                selected.action, selected.economic_score
            ),
            selection_reasoning=(
                f"{selected.action.value}: expected_recovery="
                f"₹{selected.expected_recovery_paise / 100:.0f}, "
                f"score={selected.economic_score:.2f}"
            ),
            timing_rationale="optimal timing window (contact policy approved)",
            rejected_actions=rejected_reasons,
            policy_checks_passed=policy_passed,
            policy_checks_failed=list(set(policy_failed)),
            policy_gate_result="APPROVED",
            execution_result=exec_result.state.value,
            execution_detail=exec_result.detail,
            idempotency_key=exec_result.idempotency_key,
            model_versions=self._versions.current().__dict__,
            requires_human_approval=recommendation.requires_human_approval,
            reasoning=recommendation.reasoning,
        )

        # Audit log
        self._audit.append(
            trigger_type=self.module_name,
            trigger_event=f"{selected.action.value}_EXECUTED",
            payload={
                "action": selected.action.value,
                "economic_score": selected.economic_score,
                "execution_state": exec_result.state.value,
            },
            case_id=case.case_id,
            customer_id=case.customer_id,
            action=selected.action.value,
            amount_paise=remaining,
        )

        return RecoveryModuleResult(
            case_id=case.case_id,
            module_name=self.module_name,
            selected_action=selected.action,
            execution_result=exec_result,
            reason=exec_result.detail,
        )
