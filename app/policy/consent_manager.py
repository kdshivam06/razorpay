"""Communication eligibility — TRAI consent tracking (§7.3).

A phone number existing is NOT consent.  Consent must be:
  - Explicitly GRANTED for the specific purpose (payment_recovery)
  - Tracked with timestamp
  - Revocable at any time
  - Per-channel (SMS consent ≠ voice consent)

FAILS CLOSED: UNKNOWN consent → block outbound communication.
"""

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


# Channels that require explicit consent for outbound communication
_CONSENT_REQUIRED_CHANNELS: frozenset[str] = frozenset(
    {"SMS", "WHATSAPP", "VOICE_CALL", "sms", "whatsapp", "voice"}
)

# Channels where consent is implicit (transactional email for payment recovery)
_IMPLICIT_CONSENT_CHANNELS: frozenset[str] = frozenset({"EMAIL", "email"})


class ConsentManager:
    """Owns and answers TRAI consent questions for every channel/purpose.

    In-memory store for the hackathon.  Production would use PostgreSQL.
    FAILS CLOSED: if consent is UNKNOWN, the channel is blocked.
    """

    def __init__(self) -> None:
        # (customer_id, channel, purpose) → CommunicationEligibility
        self._store: dict[tuple[str, str, str], CommunicationEligibility] = {}

    def get_eligibility(
        self, customer_id: str, channel: str, purpose: str
    ) -> CommunicationEligibility:
        """Return the consent status for a specific customer/channel/purpose.

        FAILS CLOSED: returns UNKNOWN (→ blocked) if no consent record exists,
        unless the channel has implicit consent (e.g. transactional email).
        """
        key = (customer_id, channel.upper(), purpose)
        stored = self._store.get(key)

        if stored is not None:
            return stored

        # Implicit consent for email (transactional purpose)
        if channel.upper() in {"EMAIL"}:
            return CommunicationEligibility(
                consent_status=ConsentStatus.GRANTED,
                consent_scope=purpose,
                consent_timestamp=None,
                revocation_status=ConsentStatus.UNKNOWN,
                channel=channel,
                purpose=purpose,
            )

        # FAIL CLOSED: no record = UNKNOWN = blocked
        return CommunicationEligibility(
            consent_status=ConsentStatus.UNKNOWN,
            consent_scope=purpose,
            consent_timestamp=None,
            revocation_status=ConsentStatus.UNKNOWN,
            channel=channel,
            purpose=purpose,
        )

    def has_consent(self, customer_id: str, channel: str, purpose: str) -> bool:
        """Convenience: True only if consent is explicitly GRANTED."""
        elig = self.get_eligibility(customer_id, channel, purpose)
        return elig.consent_status == ConsentStatus.GRANTED

    def record_consent(
        self,
        customer_id: str,
        channel: str,
        purpose: str,
        timestamp: datetime,
    ) -> None:
        """Record an explicit consent grant."""
        key = (customer_id, channel.upper(), purpose)
        self._store[key] = CommunicationEligibility(
            consent_status=ConsentStatus.GRANTED,
            consent_scope=purpose,
            consent_timestamp=timestamp,
            revocation_status=ConsentStatus.UNKNOWN,
            channel=channel,
            purpose=purpose,
        )

    def revoke_consent(
        self,
        customer_id: str,
        channel: str,
        purpose: str,
        timestamp: datetime,
    ) -> None:
        """Record a consent revocation. Once revoked, communication is blocked."""
        key = (customer_id, channel.upper(), purpose)
        previous = self._store.get(key)
        self._store[key] = CommunicationEligibility(
            consent_status=ConsentStatus.REVOKED,
            consent_scope=purpose,
            consent_timestamp=previous.consent_timestamp if previous else None,
            revocation_status=ConsentStatus.REVOKED,
            channel=channel,
            purpose=purpose,
        )
