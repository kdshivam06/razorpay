"""Append-only audit logger with hash chain (§13.1, §13.2, §13.6)."""

from __future__ import annotations

import dataclasses
from datetime import datetime


@dataclasses.dataclass(frozen=True)
class AuditEntry:
    """One append-only hash-chained audit record (§13.2)."""

    previous_hash: str | None
    current_hash: str
    trigger_type: str
    trigger_event: str | None
    payload_hash: str
    created_at: datetime
    case_id: str | None


class AuditLogger:
    """Append-only; never updates or deletes (§13.6). Every record chains to
    the previous via SHA256(previous_hash + payload_hash + timestamp) (§13.2).
    If an earlier record is modified, the chain breaks."""

    def append(
        self,
        *,
        trigger_type: str,
        trigger_event: str | None,
        payload: dict,
        case_id: str | None,
        obligation_id: str | None,
        customer_id: str | None,
        transaction_id: str | None,
        action: str | None,
        **attrs: object,
    ) -> AuditEntry:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §13.2, §13.6")

    def verify_chain(self) -> list[str]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §13.2")