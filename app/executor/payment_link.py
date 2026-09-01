"""Payment link lifecycle protection (§8.4)."""

from __future__ import annotations

import dataclasses

from app.contracts import PaymentLinkState


@dataclasses.dataclass(frozen=True)
class PaymentLinkRecord:
    """Full lifecycle state, NOT just active=true/false (§8.4)."""

    link_id: str
    state: PaymentLinkState
    amount_paid_paise: int
    amount_outstanding_paise: int
    expires_at_ts: float | None


class PaymentLinkLifecycle:
    """Manages CREATED → PARTIALLY_PAID → PAID → CANCELLED → EXPIRED (§8.4).

    Principles:
      existing active link → reuse it
      expired  → create a replacement
      paid     → close the recovery
      partially paid → recover only the remaining amount
    """

    def resolve_outstanding(
        self, case_id: str, invoice_amount_paise: int
    ) -> int:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.4")

    def create_or_reuse(self, case_id: str) -> PaymentLinkRecord:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.4")

    def cancel_active_link(self, case_id: str) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.4")

    def get(self, link_id: str) -> PaymentLinkRecord:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.4")