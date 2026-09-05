"""Master policy gate — AI proposes, policy gates, executor performs (§7.1).

This is the SINGLE entry point every action MUST pass through before execution.
It runs a chain of sub-gates in strict order.  If ANY gate fails, the action
is BLOCKED.  Every gate FAILS CLOSED per the Fallback Hierarchy (§2.4):
  - Redis fails → outbound financial/contact action → FAIL CLOSED
  - DB cannot guarantee state → irreversible action → BLOCK

The gate chain:
  1. Consent (TRAI) — §7.3
  2. Contact window — §7.2
  3. Customer preference (DND) — §7.4
  4. Cooldown — §7.1
  5. Fraud — §7.1 / §7.7
  6. Dispute — §7.1
  7. Reversibility / RBAC — §6.4 / §7.7
  8. Blast radius — §7.5
  9. Platform awareness — §7.6
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone

from app.contracts import Action, PolicyGateResult
from app.core.obligation import ObligationStatus
from app.core.recovery_case import RecoveryCase
from app.policy.blast_radius import BlastRadiusGuard
from app.policy.consent_manager import ConsentManager
from app.policy.contact_policy import Channel, ContactPolicyEngine
from app.policy.cooldown_manager import CooldownManager
from app.policy.customer_preferences import CustomerPreferenceEngine
from app.policy.fraud_detector import FraudDetector
from app.policy.platform_awareness import PlatformAwareness
from app.policy.reversibility import ReversibilityScorer
from app.policy.legal_basis import get_legal_basis

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class GateResult:
    """Per-gate evaluation result with legal basis."""

    gate_name: str
    result: str  # "PASS" | "FAIL" | "BLOCKED" | "SKIPPED"
    reason: str | None
    legal_basis: str
    legal_basis_description: str


@dataclasses.dataclass(frozen=True)
class PolicyEvaluation:
    """Aggregate of every check the master gate ran for one (case, action)."""

    action: Action
    result: PolicyGateResult
    gate_results: tuple[GateResult, ...]
    violations: tuple[str, ...] = ()
    human_approval_required: bool = False
    hold_until: datetime | None = None

    # Convenience properties for backward compatibility
    @property
    def passed_checks(self) -> tuple[str, ...]:
        return tuple(gr.gate_name for gr in self.gate_results if gr.result == "PASS")

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(gr.gate_name for gr in self.gate_results if gr.result in {"FAIL", "BLOCKED"})

    @property
    def blocked_reasons(self) -> tuple[str, ...]:
        return tuple(gr.reason for gr in self.gate_results if gr.result in {"FAIL", "BLOCKED"} and gr.reason)


# Actions that bypass most policy checks (internal/non-outbound)
_PASSTHROUGH_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.NO_ACTION,
        Action.WAIT,
        Action.BLOCK,
        Action.WRITE_OFF,
        Action.HUMAN_ESCALATION,
    }
)

# Mapping from Action to contact Channel for window/cooldown checks
_ACTION_TO_CHANNEL: dict[Action, Channel] = {
    Action.SEND_SMS: Channel.SMS,
    Action.SEND_EMAIL: Channel.EMAIL,
    Action.SEND_WHATSAPP: Channel.WHATSAPP,
    Action.VOICE_CALL: Channel.VOICE,
    Action.SEND_PAYMENT_LINK: Channel.SMS,  # Payment links sent via SMS
}

# Actions that are "outbound communication" — subject to consent, window, cooldown
_OUTBOUND_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.SEND_SMS,
        Action.SEND_EMAIL,
        Action.SEND_WHATSAPP,
        Action.VOICE_CALL,
        Action.SEND_PAYMENT_LINK,
    }
)

# Actions that are "financial" — subject to stricter fraud/reversibility checks
_FINANCIAL_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.RETRY_SAME_METHOD,
        Action.RETRY_ALTERNATE_METHOD,
        Action.SEND_PAYMENT_LINK,
        Action.OFFER_PARTIAL_PAYMENT,
        Action.REQUEST_PAYMENT_METHOD_UPDATE,
    }
)


class PolicyEngine:
    """The master gate evaluated for every outbound action (§7.1):

    consent | contact window | DND/preference | cooldown | fraud |
    dispute | RBAC | reversibility | blast radius | platform state

    Every gate FAILS CLOSED.  If any sub-gate cannot determine safety,
    the action is BLOCKED.
    """

    def __init__(
        self,
        consent: ConsentManager | None = None,
        contact: ContactPolicyEngine | None = None,
        preferences: CustomerPreferenceEngine | None = None,
        cooldown: CooldownManager | None = None,
        fraud: FraudDetector | None = None,
        reversibility: ReversibilityScorer | None = None,
        blast_radius: BlastRadiusGuard | None = None,
        platform: PlatformAwareness | None = None,
    ) -> None:
        self._consent = consent or ConsentManager()
        self._contact = contact or ContactPolicyEngine()
        self._preferences = preferences
        self._cooldown = cooldown
        self._fraud = fraud or FraudDetector()
        self._reversibility = reversibility or ReversibilityScorer()
        self._blast_radius = blast_radius or BlastRadiusGuard()
        self._platform = platform or PlatformAwareness()

    def evaluate(
        self,
        case: RecoveryCase,
        action: Action,
        at: datetime | None = None,
    ) -> PolicyEvaluation:
        """Run the full gate chain for a (case, action) pair.

        Returns APPROVED only if ALL gates pass.  Returns BLOCKED with
        reasons if ANY gate fails.  Returns detailed per-gate results
        with legal basis for explainability.
        """
        now = at or datetime.now(timezone.utc)
        gate_results: list[GateResult] = []
        violations: list[str] = []
        requires_human = False
        hold_until: datetime | None = None

        # Helper to add a gate result with legal basis
        def add_gate(gate_name: str, result: str, reason: str | None = None) -> None:
            legal_basis, description = get_legal_basis(gate_name)
            gate_results.append(GateResult(
                gate_name=gate_name,
                result=result,
                reason=reason,
                legal_basis=legal_basis,
                legal_basis_description=description,
            ))

        # ── Passthrough actions: add passthrough gate, mark others SKIPPED ──────────────────
        if action in _PASSTHROUGH_ACTIONS:
            add_gate("passthrough", "PASS", "Internal action — no outbound effect")
            # Mark all other gates as SKIPPED for completeness
            for gate_name in ["consent", "contact_window", "customer_preference", "cooldown",
                             "fraud", "dispute", "reversibility", "blast_radius", "platform_awareness"]:
                legal_basis, description = get_legal_basis(gate_name)
                gate_results.append(GateResult(
                    gate_name=gate_name,
                    result="SKIPPED",
                    reason=f"Passthrough action {action.value} — no outbound effect",
                    legal_basis=legal_basis,
                    legal_basis_description=description,
                ))
            return PolicyEvaluation(
                action=action,
                result=PolicyGateResult.APPROVED,
                gate_results=tuple(gate_results),
                violations=(),
                human_approval_required=False,
                hold_until=None,
            )

        # ── Gate 1: Consent (TRAI) — §7.3 ────────────────────────
        if action in _OUTBOUND_ACTIONS:
            channel = _ACTION_TO_CHANNEL.get(action)
            channel_name = channel.value if channel else action.value
            has_consent = self._consent.has_consent(
                case.customer_id, channel_name, "payment_recovery"
            )
            if has_consent:
                add_gate("consent", "PASS", f"TRAI consent granted for {channel_name}/payment_recovery")
            else:
                add_gate("consent", "FAIL", f"No TRAI consent for {channel_name}/payment_recovery")
                violations.append("TRAI_CONSENT_MISSING")
        else:
            add_gate("consent", "SKIPPED", "Not an outbound communication action")

        # ── Gate 2: Contact window — §7.2 ────────────────────────
        if action in _OUTBOUND_ACTIONS:
            channel = _ACTION_TO_CHANNEL.get(action)
            if channel:
                if self._contact.is_contact_allowed(channel, now):
                    add_gate("contact_window", "PASS", f"Within allowed window for {channel.value}")
                else:
                    next_time = self._contact.next_allowed(channel, now)
                    hold_until = next_time
                    add_gate("contact_window", "FAIL",
                        f"Outside contact window for {channel.value} — hold until {next_time}")
            else:
                add_gate("contact_window", "SKIPPED", "No channel mapping for action")
        else:
            add_gate("contact_window", "SKIPPED", "Not an outbound communication action")

        # ── Gate 3: Customer preference (DND) — §7.4 ─────────────
        if action in _OUTBOUND_ACTIONS and self._preferences:
            channel = _ACTION_TO_CHANNEL.get(action)
            if channel:
                if self._preferences.can_contact(case.customer_id, channel.value, now):
                    add_gate("customer_preference", "PASS", f"Customer prefers {channel.value} at this time")
                else:
                    add_gate("customer_preference", "FAIL", f"Customer preference blocks {channel.value} at this time")
            else:
                add_gate("customer_preference", "SKIPPED", "No channel mapping for action")
        else:
            add_gate("customer_preference", "SKIPPED", "Not an outbound action or preferences engine not configured")

        # ── Gate 4: Cooldown — §7.1 ──────────────────────────────
        if action in _OUTBOUND_ACTIONS and self._cooldown:
            channel = _ACTION_TO_CHANNEL.get(action)
            channel_name = channel.value if channel else action.value
            if self._cooldown.within_cooldown(case.customer_id, channel_name):
                remaining = self._cooldown.backoff_seconds(
                    case.customer_id, channel_name
                )
                add_gate("cooldown", "FAIL",
                    f"Cooldown active for {channel_name} ({remaining}s remaining)")
            else:
                add_gate("cooldown", "PASS", f"No cooldown for {channel_name}")
        else:
            add_gate("cooldown", "SKIPPED", "Not an outbound action or cooldown manager not configured")

        # ── Gate 5: Fraud — §7.1 / §7.7 ──────────────────────────
        fraud_verdict = self._fraud.assess(
            {"fraud_score": case.fraud_score, "dispute_count": 0}
        )
        if not self._fraud.is_action_allowed(fraud_verdict, action):
            add_gate("fraud", "FAIL",
                f"Fraud risk {fraud_verdict.risk_level.value} (score={fraud_verdict.fraud_score:.2f}) blocks {action.value}")
            violations.append("FRAUD_BLOCK")
        else:
            add_gate("fraud", "PASS", f"Fraud risk {fraud_verdict.risk_level.value} allows {action.value}")

        # ── Gate 6: Dispute — §7.1 ───────────────────────────────
        has_dispute = any(
            o.status == ObligationStatus.DISPUTED for o in case.obligations
        )
        if has_dispute and action not in _PASSTHROUGH_ACTIONS:
            add_gate("dispute", "FAIL", "Active dispute — all recovery actions halted")
            violations.append("ACTIVE_DISPUTE")
        else:
            add_gate("dispute", "PASS", "No active dispute on obligations")

        # ── Gate 7: Reversibility / RBAC — §6.4 / §7.7 ───────────
        amount = case.total_remaining()
        assessment = self._reversibility.assess(
            action,
            amount_paise=amount,
            fraud_risk_high=fraud_verdict.flagged,
        )
        if assessment.requires_human_approval:
            requires_human = True
            add_gate("reversibility", "PASS",
                f"Action {action.value} requires human approval (amount={amount}, risk={assessment.impact_level})")
        else:
            add_gate("reversibility", "PASS", f"Action {action.value} within autonomy limits")

        # ── Gate 8: Blast radius — §7.5 ──────────────────────────
        if action in (_OUTBOUND_ACTIONS | _FINANCIAL_ACTIONS):
            blast = self._blast_radius.check_global_rate(action)
            if blast.circuit_open:
                add_gate("blast_radius", "FAIL", "Circuit breaker OPEN — agent rate anomaly detected")
            elif blast.anomalous:
                add_gate("blast_radius", "FAIL", f"Rate limit breached: {', '.join(blast.breaches)}")
            else:
                add_gate("blast_radius", "PASS", "Global rate limits within bounds")
        else:
            add_gate("blast_radius", "SKIPPED", "Action not subject to blast radius limits")

        # ── Gate 9: Platform awareness — §7.6 ────────────────────
        history = self._platform.history_from_case(case)
        suppress, platform_reason = self._platform.should_suppress(history, action)
        if suppress:
            add_gate("platform_awareness", "FAIL", platform_reason)
        else:
            add_gate("platform_awareness", "PASS", "No platform duplicate action detected")

        # ── Final verdict ─────────────────────────────────────────
        has_failures = any(gr.result in {"FAIL", "BLOCKED"} for gr in gate_results)
        result = PolicyGateResult.BLOCKED if has_failures else PolicyGateResult.APPROVED

        evaluation = PolicyEvaluation(
            action=action,
            result=result,
            gate_results=tuple(gate_results),
            violations=tuple(violations),
            human_approval_required=requires_human,
            hold_until=hold_until,
        )

        logger.info(
            "Policy evaluation: case=%s action=%s result=%s gates=%s",
            case.case_id,
            action.value,
            result.value,
            [(gr.gate_name, gr.result) for gr in gate_results],
        )

        return evaluation

    def evaluate_autonomy(
        self,
        case: RecoveryCase,
        action: Action,
    ) -> PolicyEvaluation:
        """Evaluate action for autonomy level (§2.5 / §7.7).

        Combines the standard policy check with decision-risk assessment:
          confidence, amount, fraud risk, dispute risk, reversibility,
          customer sensitivity.
        """
        evaluation = self.evaluate(case, action)

        # If already blocked, return as-is
        if evaluation.result == PolicyGateResult.BLOCKED:
            return evaluation

        # Additional autonomy check — large amounts need human
        amount = case.total_remaining()
        if amount >= 50000000 and action in _FINANCIAL_ACTIONS:
            # Create a new evaluation with human_approval_required=True
            # Need to add a gate result for the autonomy check
            from app.policy.legal_basis import get_legal_basis
            legal_basis, description = get_legal_basis("reversibility")
            
            gate_results = list(evaluation.gate_results)
            # Update reversibility gate to note human approval required
            gate_results = [
                gr if gr.gate_name != "reversibility" else GateResult(
                    gate_name="reversibility",
                    result="PASS",
                    reason=f"Action {action.value} requires human approval (high value: {amount})",
                    legal_basis=legal_basis,
                    legal_basis_description=description,
                )
                for gr in gate_results
            ]
            
            return PolicyEvaluation(
                action=action,
                result=evaluation.result,
                gate_results=tuple(gate_results),
                violations=evaluation.violations,
                human_approval_required=True,
                hold_until=evaluation.hold_until,
            )

        return evaluation

    def human_approval_required(
        self,
        case: RecoveryCase,
        action: Action,
    ) -> bool:
        """Quick check: does this (case, action) require human approval?"""
        evaluation = self.evaluate_autonomy(case, action)
        return evaluation.human_approval_required
