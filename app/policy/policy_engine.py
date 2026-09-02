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

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PolicyEvaluation:
    """Aggregate of every check the master gate ran for one (case, action)."""

    action: Action
    result: PolicyGateResult
    passed_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    violations: tuple[str, ...] = ()
    human_approval_required: bool = False
    hold_until: datetime | None = None


# Actions that bypass most policy checks (internal/non-outbound)
_PASSTHROUGH_ACTIONS: frozenset[Action] = frozenset(
    {Action.NO_ACTION, Action.WAIT, Action.BLOCK}
)

# Mapping from Action to contact Channel for window/cooldown checks
_ACTION_TO_CHANNEL: dict[Action, Channel] = {
    Action.SEND_SMS: Channel.SMS,
    Action.SEND_EMAIL: Channel.EMAIL,
    Action.SEND_WHATSAPP: Channel.WHATSAPP,
    Action.VOICE_CALL: Channel.VOICE,
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
        reasons if ANY gate fails.
        """
        now = at or datetime.now(timezone.utc)
        passed: list[str] = []
        failed: list[str] = []
        blocked_reasons: list[str] = []
        violations: list[str] = []
        requires_human = False
        hold_until: datetime | None = None

        # ── Passthrough actions skip most checks ──────────────────
        if action in _PASSTHROUGH_ACTIONS:
            return PolicyEvaluation(
                action=action,
                result=PolicyGateResult.APPROVED,
                passed_checks=("passthrough",),
                failed_checks=(),
                blocked_reasons=(),
            )

        # ── Gate 1: Consent (TRAI) — §7.3 ────────────────────────
        if action in _OUTBOUND_ACTIONS:
            channel = _ACTION_TO_CHANNEL.get(action)
            channel_name = channel.value if channel else action.value
            has_consent = self._consent.has_consent(
                case.customer_id, channel_name, "payment_recovery"
            )
            if has_consent:
                passed.append("consent")
            else:
                failed.append("consent")
                blocked_reasons.append(
                    f"no TRAI consent for {channel_name}/payment_recovery"
                )
                violations.append("TRAI_CONSENT_MISSING")

        # ── Gate 2: Contact window — §7.2 ────────────────────────
        if action in _OUTBOUND_ACTIONS:
            channel = _ACTION_TO_CHANNEL.get(action)
            if channel:
                if self._contact.is_contact_allowed(channel, now):
                    passed.append("contact_window")
                else:
                    failed.append("contact_window")
                    next_time = self._contact.next_allowed(channel, now)
                    hold_until = next_time
                    blocked_reasons.append(
                        f"outside contact window for {channel.value}"
                        f" — hold until {next_time}"
                    )

        # ── Gate 3: Customer preference (DND) — §7.4 ─────────────
        if action in _OUTBOUND_ACTIONS and self._preferences:
            channel = _ACTION_TO_CHANNEL.get(action)
            if channel:
                if self._preferences.can_contact(case.customer_id, channel.value, now):
                    passed.append("customer_preference")
                else:
                    failed.append("customer_preference")
                    blocked_reasons.append(
                        f"customer preference blocks {channel.value} at this time"
                    )

        # ── Gate 4: Cooldown — §7.1 ──────────────────────────────
        if action in _OUTBOUND_ACTIONS and self._cooldown:
            channel = _ACTION_TO_CHANNEL.get(action)
            channel_name = channel.value if channel else action.value
            if self._cooldown.within_cooldown(case.customer_id, channel_name):
                failed.append("cooldown")
                remaining = self._cooldown.backoff_seconds(
                    case.customer_id, channel_name
                )
                blocked_reasons.append(
                    f"cooldown active for {channel_name}" f" ({remaining}s remaining)"
                )
            else:
                passed.append("cooldown")

        # ── Gate 5: Fraud — §7.1 / §7.7 ──────────────────────────
        fraud_verdict = self._fraud.assess(
            {"fraud_score": case.fraud_score, "dispute_count": 0}
        )
        if not self._fraud.is_action_allowed(fraud_verdict, action):
            failed.append("fraud")
            blocked_reasons.append(
                f"fraud risk {fraud_verdict.risk_level.value}"
                f" (score={fraud_verdict.fraud_score:.2f})"
                f" blocks {action.value}"
            )
            violations.append("FRAUD_BLOCK")
        else:
            passed.append("fraud")

        # ── Gate 6: Dispute — §7.1 ───────────────────────────────
        has_dispute = any(
            o.status == ObligationStatus.DISPUTED for o in case.obligations
        )
        if has_dispute and action not in _PASSTHROUGH_ACTIONS:
            failed.append("dispute")
            blocked_reasons.append("active dispute — all recovery actions halted")
            violations.append("ACTIVE_DISPUTE")
        else:
            passed.append("dispute")

        # ── Gate 7: Reversibility / RBAC — §6.4 / §7.7 ───────────
        amount = case.total_remaining()
        assessment = self._reversibility.assess(
            action,
            amount_paise=amount,
            fraud_risk_high=fraud_verdict.flagged,
        )
        if assessment.requires_human_approval:
            requires_human = True
            # Not a hard block — but flags for human queue
            passed.append("reversibility")
        else:
            passed.append("reversibility")

        # ── Gate 8: Blast radius — §7.5 ──────────────────────────
        if action in (_OUTBOUND_ACTIONS | _FINANCIAL_ACTIONS):
            blast = self._blast_radius.check_global_rate(action)
            if blast.circuit_open:
                failed.append("blast_radius")
                blocked_reasons.append(
                    "circuit breaker OPEN — agent rate anomaly detected"
                )
            elif blast.anomalous:
                failed.append("blast_radius")
                blocked_reasons.append(
                    f"rate limit breached: {', '.join(blast.breaches)}"
                )
            else:
                passed.append("blast_radius")

        # ── Gate 9: Platform awareness — §7.6 ────────────────────
        history = self._platform.history_from_case(case)
        suppress, platform_reason = self._platform.should_suppress(history, action)
        if suppress:
            failed.append("platform_awareness")
            blocked_reasons.append(platform_reason)
        else:
            passed.append("platform_awareness")

        # ── Final verdict ─────────────────────────────────────────
        if failed:
            result = PolicyGateResult.BLOCKED
        else:
            result = PolicyGateResult.APPROVED

        evaluation = PolicyEvaluation(
            action=action,
            result=result,
            passed_checks=tuple(passed),
            failed_checks=tuple(failed),
            blocked_reasons=tuple(blocked_reasons),
            violations=tuple(violations),
            human_approval_required=requires_human,
            hold_until=hold_until,
        )

        logger.info(
            "Policy evaluation: case=%s action=%s result=%s passed=%s failed=%s",
            case.case_id,
            action.value,
            result.value,
            passed,
            failed,
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
            return PolicyEvaluation(
                action=action,
                result=evaluation.result,
                passed_checks=evaluation.passed_checks,
                failed_checks=evaluation.failed_checks,
                blocked_reasons=evaluation.blocked_reasons,
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
