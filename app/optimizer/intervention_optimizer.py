"""Intervention optimizer — economic action selection (§6.1, §6.2, §6.9).

Implements the central equation from §1.1 LITERALLY:

    a* = argmax_a [ ExpectedIncrementalRecovery(a) − ActionCost(a)
                    − CustomerExperiencePenalty(a) − RiskPenalty(a) ]
    subject to: Policy(a) = TRUE

NO_ACTION is ALWAYS evaluated as a first-class candidate action.  The optimizer
never calls a Razorpay API — it only proposes.  The Policy Engine (§7) gates,
the Executor (§8) performs.
"""

from __future__ import annotations

import dataclasses
import logging

from app.contracts import (
    Action,
    CandidateAction,
    RecoveryStopReason,
)
from app.core.obligation import ObligationStatus
from app.core.recovery_case import RecoveryCase, UpliftSegment
from app.optimizer.action_suppression import ActionSuppressor
from app.optimizer.contact_fatigue import ContactFatigueEngine
from app.optimizer.recovery_economics import RecoveryEconomics
from app.policy.blast_radius import BlastRadiusGuard
from app.policy.fraud_detector import FraudDetector
from app.policy.reversibility import ReversibilityScorer
from app.revenue_risk.uplift_model import UpliftEstimates

logger = logging.getLogger(__name__)

# Minimum incremental recovery (paise) below which the financial stop-rule
# fires and the agent prefers NO_ACTION.  §6.9: "expected incremental recovery
# < minimum threshold".  Default ₹5 = 500 paise.
_MIN_INCREMENTAL_PAISE = 500


@dataclasses.dataclass(frozen=True)
class OptimizationRecommendation:
    """The optimizer's single decision for a case."""

    selected_action: Action
    candidates: list[CandidateAction]
    economic_scores: dict[Action, float]
    stopped: bool = False
    stop_reason: RecoveryStopReason | None = None
    rejected_reasons: dict[Action, str] = dataclasses.field(default_factory=dict)

    # Human approval flag — true when action is irreversible, above threshold,
    # or flagged by reversibility/fraud/blast-radius (§6.4, §7.7)
    requires_human_approval: bool = False

    # Natural-language reasoning for WHY this action was chosen (separate from
    # diagnostic rationale in E.4 — this explains the *action choice*)
    reasoning: str = ""

    @property
    def recommended_action(self) -> Action:
        """Alias for selected_action for API clarity."""
        return self.selected_action


class InterventionOptimizer:
    """Ranks all candidate actions by net economic value (§6.2) and picks the
    best policy-permitted one.  Applies the three stopping rules (§6.9) BEFORE
    selecting any action.  NO_ACTION is a first-class outcome, not a fallback.

    Architecture contract (§2.2):
        AI Proposes → Policy Engine Gates → Executor Performs
    This class is the "Proposes" step.  It never touches Razorpay.
    """

    def __init__(
        self,
        economics: RecoveryEconomics | None = None,
        suppressor: ActionSuppressor | None = None,
        fatigue: ContactFatigueEngine | None = None,
        reversibility: ReversibilityScorer | None = None,
        fraud_detector: FraudDetector | None = None,
        blast_radius: BlastRadiusGuard | None = None,
        min_incremental_paise: int = _MIN_INCREMENTAL_PAISE,
    ) -> None:
        self._econ = economics or RecoveryEconomics()
        self._suppressor = suppressor or ActionSuppressor()
        self._fatigue = fatigue or ContactFatigueEngine()
        self._reversibility = reversibility or ReversibilityScorer()
        self._fraud_detector = fraud_detector or FraudDetector()
        self._blast_radius = blast_radius or BlastRadiusGuard()
        self._min_incremental = min_incremental_paise

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def optimize(
        self,
        case: RecoveryCase,
        estimates: UpliftEstimates,
    ) -> OptimizationRecommendation:
        """Run the full §1.1 optimisation pipeline for one case.

        Steps:
        1. Check §6.9 customer/compliance stop rules.
        2. Build economic candidates (all 14 actions + NO_ACTION baseline).
        3. Apply suppression (§6.5) to filter candidates.
        4. Apply uplift-segment overrides (§5.4).
        5. Select best candidate by economic score.
        6. Apply financial stop rule (§6.9): if best < min threshold → NO_ACTION.
        """

        # ── Step 1: Customer & Compliance-Risk stop rules ──────────────
        stop = self._customer_stop_check(case)
        if stop is not None:
            return self._stopped_recommendation(stop, case, estimates)

        stop = self._compliance_risk_stop_check(case)
        if stop is not None:
            return self._stopped_recommendation(stop, case, estimates)

        # ── Step 2: Build all candidates with economics ─────────────────
        amount_remaining = case.total_remaining()
        candidates = self.build_candidates(case, estimates)

        # ── Step 3: Apply suppression ──────────────────────────────────
        rejected_reasons: dict[Action, str] = {}
        allowed: list[CandidateAction] = []

        for candidate in candidates:
            verdict = self._suppressor.check(case, candidate.action)
            if verdict.suppressed:
                rejected_reasons[candidate.action] = (
                    f"suppressed: {', '.join(verdict.suppress_reasons)}"
                )
            else:
                allowed.append(candidate)

        # NO_ACTION must always survive suppression
        if not any(c.action == Action.NO_ACTION for c in allowed):
            no_action = next(
                (c for c in candidates if c.action == Action.NO_ACTION), None
            )
            if no_action:
                allowed.append(no_action)

        # ── Step 4: Uplift-segment overrides ──────────────────────────
        segment = estimates.uplift_segment
        if segment in {UpliftSegment.SURE_THING, UpliftSegment.SLEEPING_DOG}:
            # These segments should receive NO_ACTION (§5.4)
            for c in allowed:
                if c.action != Action.NO_ACTION:
                    reason = (
                        "Sure Thing — high natural pay probability, no intervention needed"
                        if segment == UpliftSegment.SURE_THING
                        else "Sleeping Dog — intervention may worsen outcome"
                    )
                    rejected_reasons[c.action] = reason
            # Force NO_ACTION
            no_action_candidate = next(
                (c for c in allowed if c.action == Action.NO_ACTION), None
            )
            if no_action_candidate is None:
                no_action_candidate = _make_no_action()
            return OptimizationRecommendation(
                selected_action=Action.NO_ACTION,
                candidates=candidates,
                economic_scores={c.action: c.economic_score for c in candidates},
                stopped=False,
                stop_reason=None,
                rejected_reasons=rejected_reasons,
            )

        # ── Step 5: Contact fatigue check ─────────────────────────────
        fatigue_features = _extract_fatigue_features(case)
        fatigue_assessment = self._fatigue.score(fatigue_features)
        if self._fatigue.should_pause_automation(fatigue_assessment):
            for c in allowed:
                if c.action not in {
                    Action.NO_ACTION,
                    Action.WAIT,
                    Action.HUMAN_ESCALATION,
                    Action.BLOCK,
                }:
                    rejected_reasons[c.action] = (
                        f"contact fatigue {fatigue_assessment.level.value}: "
                        f"{', '.join(fatigue_assessment.reasons)}"
                    )
            # Fall to WAIT or NO_ACTION
            allowed = [
                c
                for c in allowed
                if c.action in {Action.NO_ACTION, Action.WAIT, Action.HUMAN_ESCALATION}
            ]
            if not allowed:
                allowed = [_make_no_action()]

        # ── Step 6: Select best by economic score ─────────────────────
        best = self.select_best(allowed)

        # ── Step 7: Financial stop rule ──────────────────────────────
        financial_stop = self.financial_stop_check(
            best, amount_remaining, self._min_incremental
        )
        if financial_stop is not None:
            rejected_reasons[best.action] = (
                f"financial stop: incremental recovery ₹{best.expected_recovery_paise / 100:.0f} "
                f"below minimum ₹{self._min_incremental / 100:.0f}"
            )
            return OptimizationRecommendation(
                selected_action=Action.NO_ACTION,
                candidates=candidates,
                economic_scores={c.action: c.economic_score for c in candidates},
                stopped=True,
                stop_reason=financial_stop,
                rejected_reasons=rejected_reasons,
                requires_human_approval=False,
                reasoning="Financial stop rule triggered — expected incremental recovery below minimum threshold.",
            )

        # Compute human approval requirement from reversibility, fraud, blast radius
        fraud_verdict = self._fraud_detector.assess({"fraud_score": case.fraud_score, "dispute_count": 0})
        reversibility_assessment = self._reversibility.assess(
            best.action,
            amount_paise=amount_remaining,
            fraud_risk_high=fraud_verdict.risk_level in {fraud_verdict.risk_level.HIGH, fraud_verdict.risk_level.CRITICAL},
        )
        blast_status = self._blast_radius.check_global_rate(best.action)

        requires_human = (
            reversibility_assessment.requires_human_approval
            or fraud_verdict.risk_level in {fraud_verdict.risk_level.HIGH, fraud_verdict.risk_level.CRITICAL}
            or blast_status.circuit_open
        )

        # Build reasoning string for the action choice
        reasoning = self._build_reasoning(best, case, estimates, segment, requires_human)

        logger.info(
            "Case %s: selected %s (score=%.2f, uplift=%.4f, segment=%s, human=%s)",
            case.case_id,
            best.action.value,
            best.economic_score,
            estimates.per_action_uplift.get(best.action, 0),
            segment,
            requires_human,
        )

        return OptimizationRecommendation(
            selected_action=best.action,
            candidates=candidates,
            economic_scores={c.action: c.economic_score for c in candidates},
            stopped=False,
            stop_reason=None,
            rejected_reasons=rejected_reasons,
            requires_human_approval=requires_human,
            reasoning=reasoning,
        )

    # ------------------------------------------------------------------
    # Candidate building
    # ------------------------------------------------------------------

    def build_candidates(
        self,
        case: RecoveryCase,
        estimates: UpliftEstimates,
    ) -> list[CandidateAction]:
        """Build all candidate actions scored by the economics engine.

        Delegates to RecoveryEconomics.score_candidates which computes the
        §1.1 inner expression for every action in the Action enum.
        """
        amount_remaining = case.total_remaining()
        return self._econ.score_candidates(estimates, amount_remaining)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select_best(self, candidates: list[CandidateAction]) -> CandidateAction:
        """Select the single best candidate by economic score (§6.2).

        Ties are broken in favour of lower-cost actions (lower CX penalty).
        If all candidates have negative scores, NO_ACTION wins.
        """
        if not candidates:
            return _make_no_action()

        # Sort by economic_score descending, then by cx_penalty ascending (tie break)
        ranked = sorted(
            candidates,
            key=lambda c: (c.economic_score, -c.cx_penalty_paise),
            reverse=True,
        )
        best = ranked[0]

        # If the best has a negative score, prefer NO_ACTION
        if best.economic_score < 0:
            no_action = next(
                (c for c in candidates if c.action == Action.NO_ACTION), None
            )
            if no_action is not None:
                return no_action

        return best

    # ------------------------------------------------------------------
    # Stopping rules (§6.9)
    # ------------------------------------------------------------------

    def financial_stop_check(
        self,
        best: CandidateAction,
        amount_remaining: int,
        minimum_incremental_paise: int,
    ) -> RecoveryStopReason | None:
        """Financial stop: expected incremental recovery < minimum threshold.

        This prevents spending resources on cases where the payoff doesn't
        justify any intervention (§6.9).
        """
        if best.action in {
            Action.NO_ACTION,
            Action.WAIT,
            Action.BLOCK,
            Action.HUMAN_ESCALATION,
        }:
            return None  # these actions don't cost anything meaningful
        if best.expected_recovery_paise < minimum_incremental_paise:
            return RecoveryStopReason.FINANCIAL
        return None

    def _customer_stop_check(self, case: RecoveryCase) -> RecoveryStopReason | None:
        """Customer stop: opt-out, contact fatigue, wrong person, active human.

        §6.9: these are customer-protection reasons to stop.
        """
        # Opt-out check
        for event in case.audit_events:
            if event.get("type") == "OPT_OUT" or event.get("opt_out"):
                return RecoveryStopReason.CUSTOMER

        # Wrong-person flag
        for event in case.audit_events:
            if event.get("type") == "WRONG_PERSON" or event.get("identity_conflict"):
                return RecoveryStopReason.CUSTOMER

        return None

    def _compliance_risk_stop_check(
        self, case: RecoveryCase
    ) -> RecoveryStopReason | None:
        """Compliance/risk stop: dispute, fraud, mandate customer-revoked, policy violation.

        §6.9: these are hard regulatory/risk reasons to stop all recovery.
        """
        # Dispute on any obligation
        for obligation in case.obligations:
            if obligation.status == ObligationStatus.DISPUTED:
                return RecoveryStopReason.COMPLIANCE_RISK

        # Fraud block
        for obligation in case.obligations:
            if obligation.status == ObligationStatus.BLOCKED:
                return RecoveryStopReason.COMPLIANCE_RISK

        # High fraud score
        if case.fraud_score >= 0.8:
            return RecoveryStopReason.COMPLIANCE_RISK

        # Customer-revoked mandate = PERMANENT STOP (RBI compliance; §9.3).
        # Distinguish from bank-revoked (safe to send re-auth) via error.source.
        for comm in case.communications:
            error_source = str(comm.get("error_source", "")).lower()
            error_desc = str(comm.get("error_description", "")).lower()
            if error_source == "customer" or (
                "customer" in error_desc and "revoked" in error_desc
            ):
                return RecoveryStopReason.COMPLIANCE_RISK

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_reasoning(
        self,
        best: CandidateAction,
        case: RecoveryCase,
        estimates: UpliftEstimates,
        segment: UpliftSegment,
        requires_human: bool,
    ) -> str:
        """Build natural-language reasoning for the action choice (E.5)."""
        action = best.action
        uplift = estimates.per_action_uplift.get(action, 0.0)
        nat_prob = case.natural_pay_probability
        amount = case.total_remaining()

        if action == Action.NO_ACTION:
            if segment == UpliftSegment.SURE_THING:
                return f"No intervention needed — customer has high natural payment probability ({nat_prob:.0%}). Intervening would waste contact budget."
            if segment == UpliftSegment.SLEEPING_DOG:
                return "Intervention may reduce payment probability for this Sleeping Dog segment. Best to wait."
            if segment == UpliftSegment.LOST_CAUSE:
                return f"Low natural payment ({nat_prob:.0%}) and low incremental uplift. Contact budget better spent elsewhere."
            return f"No action has positive incremental value over natural payment ({nat_prob:.0%})."

        if action == Action.WAIT:
            return "Waiting for optimal retry window — predicted best time increases payment probability."

        if action == Action.HUMAN_ESCALATION:
            return f"Case requires human review — {('high value' if amount >= 10000000 else 'complex situation')}."

        if action == Action.BLOCK:
            return "Recovery blocked — active dispute or fraud risk."

        # For outbound actions, explain the economic rationale
        uplift_pct = uplift * 100
        incr_recovery = best.expected_recovery_paise - round(nat_prob * amount)
        human_note = " Requires human approval." if requires_human else ""

        if action == Action.SEND_PAYMENT_LINK:
            return (
                f"Payment link offers highest incremental uplift (+{uplift_pct:.0f}% → "
                f"₹{incr_recovery/100:,.0f} incremental recovery) for {segment.value} segment.{human_note}"
            )
        if action == Action.SEND_SMS:
            return (
                f"SMS provides cost-effective nudge (+{uplift_pct:.0f}% uplift, "
                f"₹{incr_recovery/100:,.0f} incremental) for {segment.value} segment.{human_note}"
            )
        if action == Action.SEND_WHATSAPP:
            return (
                f"WhatsApp preferred channel for this customer (+{uplift_pct:.0f}% uplift, "
                f"₹{incr_recovery/100:,.0f} incremental).{human_note}"
            )
        if action == Action.SEND_EMAIL:
            return (
                f"Email provides documented outreach (+{uplift_pct:.0f}% uplift, "
                f"₹{incr_recovery/100:,.0f} incremental).{human_note}"
            )
        if action == Action.VOICE_CALL:
            return (
                f"Voice call for high-touch recovery (+{uplift_pct:.0f}% uplift, "
                f"₹{incr_recovery/100:,.0f} incremental).{human_note}"
            )
        if action in {Action.RETRY_SAME_METHOD, Action.RETRY_ALTERNATE_METHOD}:
            return (
                f"Retry {action.value.replace('RETRY_', '').lower()} — "
                f"transient failure with {uplift_pct:.0f}% uplift.{human_note}"
            )
        if action == Action.OFFER_PARTIAL_PAYMENT:
            return f"Partial payment offer for {segment.value} — customer may pay portion.{human_note}"
        if action == Action.REQUEST_PAYMENT_METHOD_UPDATE:
            return f"Payment method update needed — card expired or invalid.{human_note}"
        if action == Action.CREATE_PTP:
            return f"Promise-to-pay created based on customer commitment.{human_note}"

        return f"Selected {action.value} for {segment.value} segment ({uplift_pct:.0f}% uplift).{human_note}"

    def _stopped_recommendation(
        self,
        reason: RecoveryStopReason,
        case: RecoveryCase,
        estimates: UpliftEstimates,
    ) -> OptimizationRecommendation:
        """Build a recommendation for a stopped case — NO_ACTION + reason."""
        candidates = self.build_candidates(case, estimates)
        return OptimizationRecommendation(
            selected_action=Action.NO_ACTION,
            candidates=candidates,
            economic_scores={c.action: c.economic_score for c in candidates},
            stopped=True,
            stop_reason=reason,
            rejected_reasons={
                c.action: f"stop rule: {reason.value}"
                for c in candidates
                if c.action != Action.NO_ACTION
            },
            requires_human_approval=False,
            reasoning=f"Stopped by {reason.value.lower()} rule — no automated action permitted.",
        )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _make_no_action() -> CandidateAction:
    """Create a zero-cost NO_ACTION candidate."""
    return CandidateAction(
        action=Action.NO_ACTION,
        expected_recovery_paise=0,
        communication_cost_paise=0,
        operational_cost_paise=0,
        risk_penalty_paise=0,
        cx_penalty_paise=0,
        economic_score=0.0,
    )


def _extract_fatigue_features(case: RecoveryCase) -> dict[str, float]:
    """Extract contact-fatigue features from the case's communication log.

    In a full implementation this would query Redis/PG for time-windowed
    counts. For the hackathon we derive from the in-memory case object.
    """
    comms = case.communications
    total = float(len(comms))
    ignored = sum(1 for c in comms if not c.get("responded"))
    negative = sum(1 for c in comms if c.get("negative_reply"))
    opt_outs = sum(1 for c in comms if c.get("opt_out"))
    complaints = sum(1 for c in comms if c.get("complaint"))
    successful = sum(1 for c in comms if c.get("success"))

    return {
        "contacts_last_1h": min(total, 5),  # approximate
        "contacts_last_24h": total,
        "contacts_last_7d": total,
        "ignored_contacts": float(ignored),
        "negative_replies": float(negative),
        "opt_outs": float(opt_outs),
        "complaints": float(complaints),
        "successful_contacts": float(successful),
        "total_contacts": total,
    }
