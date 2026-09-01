"""Communication eligibility — TRAI consent tracking (§7.3)."""

from __future__ import annotations

import dataclasses
import enum
from datetime import datetime


class ConsentStatus(str, enum.Enum):
    GRANTED = "GRANTED"
    REVOKED = "REVOKED"
    UNKNOWN = "UNKNOWN"


@dataclasses.dataclass(frozen=True)
class CommunicationEligibility:
    """§7.3 — a phone number existing is NOT consent."""

    consent_status: ConsentStatus
    consent_scope: str
    consent_timestamp: datetime | None
    revocation_status: ConsentStatus
    channel: str
    purpose: str


class ConsentManager:
    """Owns and answers TRAI consent questions for every channel/purpose."""

    def get_eligibility(self, customer_id: str, channel: str, purpose: str) -> CommunicationEligibility:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.3")

    def record_consent(self, customer_id: str, channel: str, purpose: str, timestamp: datetime) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.3")

    def revoke_consent(self, customer_id: str, channel: str, purpose: str, timestamp: datetime) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.3")