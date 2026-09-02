"""Pre-action reconciliation — strongest demo feature (§8.2).

Called immediately before every executor action. Catches "already paid" races.

  AI: Send SMS
  Ledger: PAID 2 seconds ago
  → CANCEL ACTION

Also checks for disputes, refunds, and other state changes that make the
proposed action invalid.
"""

from __future__ import annotations

import dataclasses
import logging

from app.contracts import Action
from app.core.obligation import ObligationStatus
from app.core.recovery_case import RecoveryCase

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PreActionVerdict:
    """Reconciliation verdict BEFORE an action executes."""

    allow: bool
    reason: str
    fresh_state: str | None = None
    cancelled_because: str | None = None


# Actions exempt from pre-action reconciliation (internal / non-outbound)
_EXEMPT_ACTIONS: frozenset[Action] = frozenset(
    {Action.NO_ACTION, Action.WAIT, Action.BLOCK, Action.HUMAN_ESCALATION}
)


class PreActionReconciler:
    """Strongest demo feature (§8.2): before SMS/Email/Voice/Link/Retry,
    re-check the ledger — if the case is PAID 2 seconds ago, CANCEL the action.

    This prevents the embarrassing scenario where the agent sends a "please pay"
    SMS to a customer who literally just paid.
    """

    def check(self, case: RecoveryCase, action: Action) -> PreActionVerdict:
        """Run all pre-action reconciliation checks.

        Returns allow=True if the action should proceed, allow=False with
        a reason if the action should be cancelled.
        """
        # Exempt actions always pass
        if action in _EXEMPT_ACTIONS:
            return PreActionVerdict(
                allow=True,
                reason="exempt action — no reconciliation needed",
                fresh_state=case.state.value,
            )

        # ── Check 1: Already fully paid ───────────────────────────
        remaining = case.total_remaining()
        if remaining <= 0:
            logger.info(
                "PRE-ACTION CANCEL: case %s already paid (remaining=₹%s). "
                "Cancelling %s.",
                case.case_id,
                remaining / 100,
                action.value,
            )
            return PreActionVerdict(
                allow=False,
                reason="case already fully paid",
                fresh_state="RECOVERED",
                cancelled_because="already_paid",
            )

        # ── Check 2: Active dispute — halt all recovery ──────────
        has_dispute = any(
            o.status == ObligationStatus.DISPUTED for o in case.obligations
        )
        if has_dispute:
            logger.info(
                "PRE-ACTION CANCEL: case %s has active dispute. " "Cancelling %s.",
                case.case_id,
                action.value,
            )
            return PreActionVerdict(
                allow=False,
                reason="active dispute — all recovery halted",
                fresh_state="DISPUTED",
                cancelled_because="active_dispute",
            )

        # ── Check 3: All obligations written off ────────────────────
        all_written_off = (
            all(o.status == ObligationStatus.WRITTEN_OFF for o in case.obligations)
            and len(case.obligations) > 0
        )
        if all_written_off:
            logger.info(
                "PRE-ACTION CANCEL: case %s fully written off. Cancelling %s.",
                case.case_id,
                action.value,
            )
            return PreActionVerdict(
                allow=False,
                reason="case fully written off",
                fresh_state="WRITTEN_OFF",
                cancelled_because="fully_written_off",
            )

        # ── Check 4: Recovery lock held by another path ──────────
        if case.recovery_lock:
            # Another recovery path is active — don't interfere
            logger.info(
                "PRE-ACTION CANCEL: case %s recovery lock held. Cancelling %s.",
                case.case_id,
                action.value,
            )
            return PreActionVerdict(
                allow=False,
                reason="recovery lock held by another path",
                fresh_state=case.state.value,
                cancelled_because="recovery_lock",
            )

        # ── Check 5: Payment link already paid (for SEND_PAYMENT_LINK) ──
        if action == Action.SEND_PAYMENT_LINK:
            for pl in case.payment_links:
                if pl.get("state") == "PAID":
                    logger.info(
                        "PRE-ACTION CANCEL: case %s has a PAID payment link. "
                        "Cancelling %s.",
                        case.case_id,
                        action.value,
                    )
                    return PreActionVerdict(
                        allow=False,
                        reason="existing payment link already paid",
                        fresh_state=case.state.value,
                        cancelled_because="payment_link_paid",
                    )

        # ── All checks passed ────────────────────────────────────
        return PreActionVerdict(
            allow=True,
            reason="pre-action reconciliation passed",
            fresh_state=case.state.value,
        )
