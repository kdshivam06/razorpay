"""Action suppression — pre-execution suppression checks (§6.5, §13.5).

Before executing ANY action, we check a battery of conditions:
  Already paid? Already contacted recently? Already generated link?
  Already in human conversation? Already has active PTP? Already disputed?
  Already opted out?

Every suppression is logged — suppression is part of the agent's value.
"""

from __future__ import annotations

import dataclasses

from app.contracts import Action
from app.core.obligation import ObligationStatus
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class SuppressionCheck:
    """One suppression candidate and its verdict."""

    reason: str
    suppress: bool
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class SuppressionVerdict:
    """Every suppression is logged — suppression is part of the value (§6.5)."""

    suppressed: bool
    checks: tuple[SuppressionCheck, ...]
    suppress_reasons: tuple[str, ...]


# Actions that are always allowed regardless of suppression
_ALWAYS_ALLOWED: frozenset[Action] = frozenset(
    {
        Action.NO_ACTION,
        Action.WAIT,
        Action.BLOCK,
        Action.HUMAN_ESCALATION,
    }
)


class ActionSuppressor:
    """Blocks an action before execution when any §6.5 condition holds:

    already paid / contacted recently / active link / human conversation /
    active PTP / disputed / opted out
    """

    def check(self, case: RecoveryCase, action: Action) -> SuppressionVerdict:
        """Run all suppression checks against the case for the given action.

        Returns a SuppressionVerdict with suppressed=True if any check fires.
        """
        if action in _ALWAYS_ALLOWED:
            return SuppressionVerdict(
                suppressed=False,
                checks=(
                    SuppressionCheck(
                        reason="always_allowed",
                        suppress=False,
                        detail=f"{action.value} exempt from suppression",
                    ),
                ),
                suppress_reasons=(),
            )

        checks: list[SuppressionCheck] = []

        # 1. Already paid?
        all_recovered = (
            all(o.status == ObligationStatus.RECOVERED for o in case.obligations)
            if case.obligations
            else False
        )
        checks.append(
            SuppressionCheck(
                reason="already_paid",
                suppress=all_recovered,
                detail=(
                    "all obligations RECOVERED"
                    if all_recovered
                    else "obligations outstanding"
                ),
            )
        )

        # 2. Already disputed?
        any_disputed = any(
            o.status == ObligationStatus.DISPUTED for o in case.obligations
        )
        checks.append(
            SuppressionCheck(
                reason="active_dispute",
                suppress=any_disputed,
                detail=(
                    "obligation in DISPUTED state" if any_disputed else "no disputes"
                ),
            )
        )

        # 3. Already blocked (fraud)?
        any_blocked = any(
            o.status == ObligationStatus.BLOCKED for o in case.obligations
        )
        checks.append(
            SuppressionCheck(
                reason="fraud_blocked",
                suppress=any_blocked,
                detail=(
                    "obligation BLOCKED (fraud/risk)" if any_blocked else "not blocked"
                ),
            )
        )

        # 4. Active PTP hold?
        any_ptp = any(o.status == ObligationStatus.PTP_HOLD for o in case.obligations)
        is_outbound = action in {
            Action.SEND_SMS,
            Action.SEND_EMAIL,
            Action.SEND_WHATSAPP,
            Action.VOICE_CALL,
            Action.SEND_PAYMENT_LINK,
            Action.RETRY_SAME_METHOD,
            Action.RETRY_ALTERNATE_METHOD,
        }
        suppress_for_ptp = any_ptp and is_outbound
        checks.append(
            SuppressionCheck(
                reason="active_ptp",
                suppress=suppress_for_ptp,
                detail=(
                    "obligation on PTP_HOLD — honour promise"
                    if suppress_for_ptp
                    else "no active PTP"
                ),
            )
        )

        # 5. Already has active payment link? (don't create duplicate)
        has_active_link = bool(case.payment_links) and any(
            link.get("state") in {"CREATED", "PARTIALLY_PAID"}
            for link in case.payment_links
        )
        link_action = action == Action.SEND_PAYMENT_LINK
        suppress_link = has_active_link and link_action
        checks.append(
            SuppressionCheck(
                reason="active_payment_link",
                suppress=suppress_link,
                detail=(
                    "active payment link exists — reuse, don't duplicate"
                    if suppress_link
                    else "no active link conflict"
                ),
            )
        )

        # 6. Recently contacted? (basic recency check from communications log)
        recent_comm = _has_recent_communication(case, action)
        checks.append(
            SuppressionCheck(
                reason="recently_contacted",
                suppress=recent_comm,
                detail=(
                    "same action sent recently"
                    if recent_comm
                    else "not recently contacted via this channel"
                ),
            )
        )

        # 7. Customer opted out?
        opted_out = _has_opt_out(case)
        suppress_opted = opted_out and is_outbound
        checks.append(
            SuppressionCheck(
                reason="customer_opted_out",
                suppress=suppress_opted,
                detail="customer opt-out recorded" if suppress_opted else "no opt-out",
            )
        )

        # 8. Already in human conversation?
        in_human = any(
            d.get("action") == Action.HUMAN_ESCALATION.value
            and d.get("outcome") in {None, "pending", "PENDING"}
            for d in case.decisions
        )
        suppress_human = in_human and is_outbound
        checks.append(
            SuppressionCheck(
                reason="active_human_conversation",
                suppress=suppress_human,
                detail=(
                    "case is with a human agent"
                    if suppress_human
                    else "no active human conversation"
                ),
            )
        )

        # Build final verdict
        suppress_reasons = tuple(c.reason for c in checks if c.suppress)
        suppressed = len(suppress_reasons) > 0

        return SuppressionVerdict(
            suppressed=suppressed,
            checks=tuple(checks),
            suppress_reasons=suppress_reasons,
        )

    def suppressed_actions(self, case: RecoveryCase) -> list[str]:
        """Return list of action names that would be suppressed for this case.

        Used by the prevention log (§13.5) and "What Agent Prevented" display.
        """
        suppressed: list[str] = []
        for action in Action:
            if action in _ALWAYS_ALLOWED:
                continue
            verdict = self.check(case, action)
            if verdict.suppressed:
                suppressed.append(action.value)
        return suppressed


def _has_recent_communication(case: RecoveryCase, action: Action) -> bool:
    """Simple recency check — if the last communication used the same action,
    suppress.  A proper implementation uses Redis cooldowns (policy track).
    """
    if not case.communications:
        return False
    last = case.communications[-1]
    return last.get("action") == action.value


def _has_opt_out(case: RecoveryCase) -> bool:
    """Check if any communication or audit event records an opt-out."""
    for comm in case.communications:
        if comm.get("opt_out"):
            return True
    for event in case.audit_events:
        if event.get("type") == "OPT_OUT" or event.get("opt_out"):
            return True
    return False
