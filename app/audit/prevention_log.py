"""Prevention log — 'what the agent prevented' (§13.5).

This dramatically strengthens the demo. Every time an action is suppressed
or blocked by policy, reconciliation, or dedup — log it here.

Examples from §13.5:
  Duplicate SMS prevented
  Duplicate Payment Link prevented
  Retry after payment prevented
  Retry against failed bank prevented
  Recovery after dispute prevented
  Voice call outside allowed period prevented
  Duplicate webhook prevented
  High-value autonomous action prevented
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PreventionRecord:
    """One logged prevention event."""

    case_id: str
    prevented_action: str
    reason: str
    category: str
    amount_saved_paise: int | None = None
    timestamp: float = dataclasses.field(default_factory=time.time)


# Prevention categories for summary reporting
class PreventionCategory:
    DUPLICATE_COMMUNICATION = "duplicate_communication"
    DUPLICATE_PAYMENT_LINK = "duplicate_payment_link"
    RETRY_AFTER_PAYMENT = "retry_after_payment"
    RETRY_FAILED_BANK = "retry_failed_bank"
    RECOVERY_AFTER_DISPUTE = "recovery_after_dispute"
    OUT_OF_WINDOW = "out_of_window"
    DUPLICATE_WEBHOOK = "duplicate_webhook"
    HIGH_VALUE_AUTONOMOUS = "high_value_autonomous"
    FRAUD_BLOCK = "fraud_block"
    COOLDOWN_ACTIVE = "cooldown_active"
    CONSENT_MISSING = "consent_missing"
    CUSTOMER_PREFERENCE = "customer_preference"
    PLATFORM_DUPLICATE = "platform_duplicate"
    CIRCUIT_BREAKER = "circuit_breaker"
    ALREADY_PAID = "already_paid"


class PreventionLog:
    """Logs every action the agent PREVENTED (§13.5).

    This is part of the agent's value, not a footnote. Every suppressed
    or blocked action is recorded with its reason and category.
    """

    def __init__(self) -> None:
        self._records: list[PreventionRecord] = []

    def log(
        self,
        case_id: str,
        prevented_action: str,
        reason: str,
        category: str = "",
        amount_saved_paise: int | None = None,
    ) -> PreventionRecord:
        """Log a prevented action."""
        if not category:
            category = self._infer_category(reason)

        record = PreventionRecord(
            case_id=case_id,
            prevented_action=prevented_action,
            reason=reason,
            category=category,
            amount_saved_paise=amount_saved_paise,
        )
        self._records.append(record)

        logger.info(
            "PREVENTED: %s for case %s — %s [%s] (saved: ₹%s)",
            prevented_action,
            case_id,
            reason,
            category,
            (amount_saved_paise or 0) / 100,
        )
        return record

    def summary(self) -> dict[str, object]:
        """Aggregate prevention statistics for the demo dashboard."""
        by_category: dict[str, int] = defaultdict(int)
        by_action: dict[str, int] = defaultdict(int)
        total_saved = 0

        for record in self._records:
            by_category[record.category] += 1
            by_action[record.prevented_action] += 1
            if record.amount_saved_paise:
                total_saved += record.amount_saved_paise

        return {
            "total_preventions": len(self._records),
            "total_saved_paise": total_saved,
            "total_saved_display": f"₹{total_saved / 100:,.0f}",
            "by_category": dict(by_category),
            "by_action": dict(by_action),
            "recent": [
                {
                    "case_id": r.case_id,
                    "action": r.prevented_action,
                    "reason": r.reason,
                    "category": r.category,
                }
                for r in self._records[-10:]
            ],
        }

    def for_case(self, case_id: str) -> list[PreventionRecord]:
        """Get all prevention records for a specific case."""
        return [r for r in self._records if r.case_id == case_id]

    @staticmethod
    def _infer_category(reason: str) -> str:
        """Infer a category from the reason string."""
        reason_lower = reason.lower()
        if "duplicate" in reason_lower or "platform already" in reason_lower:
            return PreventionCategory.DUPLICATE_COMMUNICATION
        if "already paid" in reason_lower or "case already" in reason_lower:
            return PreventionCategory.ALREADY_PAID
        if "dispute" in reason_lower:
            return PreventionCategory.RECOVERY_AFTER_DISPUTE
        if "contact window" in reason_lower or "outside" in reason_lower:
            return PreventionCategory.OUT_OF_WINDOW
        if "cooldown" in reason_lower:
            return PreventionCategory.COOLDOWN_ACTIVE
        if "consent" in reason_lower:
            return PreventionCategory.CONSENT_MISSING
        if "fraud" in reason_lower:
            return PreventionCategory.FRAUD_BLOCK
        if "circuit" in reason_lower:
            return PreventionCategory.CIRCUIT_BREAKER
        if "preference" in reason_lower:
            return PreventionCategory.CUSTOMER_PREFERENCE
        return "other"
