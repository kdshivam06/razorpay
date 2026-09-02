"""Payment link lifecycle protection (§8.4).

Principles:
  existing active link → reuse it
  expired  → create a replacement
  paid     → close the recovery
  partially paid → recover only the remaining amount
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid

from app.contracts import PaymentLinkState

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PaymentLinkRecord:
    """Full lifecycle state, NOT just active=true/false (§8.4)."""

    link_id: str
    state: PaymentLinkState
    amount_paid_paise: int
    amount_outstanding_paise: int
    expires_at_ts: float | None


class PaymentLinkLifecycle:
    """Manages CREATED → PARTIALLY_PAID → PAID → CANCELLED → EXPIRED (§8.4)."""

    def __init__(self) -> None:
        # mock db: case_id -> list[PaymentLinkRecord]
        self._links: dict[str, list[PaymentLinkRecord]] = {}

    def resolve_outstanding(self, case_id: str, invoice_amount_paise: int) -> int:
        """Calculate what remains to be collected given all active/partially paid links."""
        links = self._links.get(case_id, [])
        paid = sum(link.amount_paid_paise for link in links)
        return max(0, invoice_amount_paise - paid)

    def create_or_reuse(self, case_id: str, amount_paise: int = 0) -> PaymentLinkRecord:
        """Reuse an active link if it exists and covers the amount, else create."""
        links = self._links.setdefault(case_id, [])

        # 1. Check for existing active link we can reuse
        for link in links:
            if link.state in {
                PaymentLinkState.CREATED,
                PaymentLinkState.PARTIALLY_PAID,
            }:
                if link.expires_at_ts and link.expires_at_ts < time.time():
                    # Link expired in reality, update state
                    self._update_state(case_id, link.link_id, PaymentLinkState.EXPIRED)
                    continue

                if link.amount_outstanding_paise >= amount_paise:
                    logger.info(
                        "Reusing existing active payment link %s for case %s",
                        link.link_id,
                        case_id,
                    )
                    return link

        # 2. Need a new link
        logger.info(
            "Creating new payment link for case %s (amount: ₹%s)",
            case_id,
            amount_paise / 100,
        )
        new_link = PaymentLinkRecord(
            link_id=f"plink_{uuid.uuid4().hex[:14]}",
            state=PaymentLinkState.CREATED,
            amount_paid_paise=0,
            amount_outstanding_paise=amount_paise,
            expires_at_ts=time.time() + 86400 * 3,  # 3 days default expiry
        )
        links.append(new_link)
        return new_link

    def cancel_active_link(self, case_id: str) -> None:
        """Cancel any currently active payment links for this case."""
        links = self._links.get(case_id, [])
        for link in links:
            if link.state in {
                PaymentLinkState.CREATED,
                PaymentLinkState.PARTIALLY_PAID,
            }:
                logger.info(
                    "Cancelling active payment link %s for case %s",
                    link.link_id,
                    case_id,
                )
                self._update_state(case_id, link.link_id, PaymentLinkState.CANCELLED)

    def get(self, link_id: str) -> PaymentLinkRecord:
        """Get a payment link by ID."""
        for links in self._links.values():
            for link in links:
                if link.link_id == link_id:
                    return link
        raise ValueError(f"Payment link {link_id} not found")

    def _update_state(
        self, case_id: str, link_id: str, new_state: PaymentLinkState
    ) -> None:
        links = self._links.get(case_id, [])
        for i, link in enumerate(links):
            if link.link_id == link_id:
                links[i] = dataclasses.replace(link, state=new_state)
                break
