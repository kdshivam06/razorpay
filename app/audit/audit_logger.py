"""Append-only audit logger with cryptographic hash chain (§13.1, §13.2, §13.6).

Every record chains to the previous via:
  current_hash = SHA256(previous_hash + payload_hash + timestamp)

If someone modifies an earlier record, the chain breaks.

The DB trigger (Track A) enforces append-only at the DB level, but
this module verifies the chain in application code — don't just trust
the trigger.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import uuid
from datetime import datetime

from app.core.clock import clock

logger = logging.getLogger(__name__)

_GENESIS_HASH = "0" * 64  # Genesis block hash


@dataclasses.dataclass(frozen=True)
class AuditEntry:
    """One append-only hash-chained audit record (§13.2)."""

    entry_id: str
    previous_hash: str
    current_hash: str
    trigger_type: str
    trigger_event: str | None
    payload_hash: str
    created_at: datetime
    case_id: str | None
    obligation_id: str | None = None
    customer_id: str | None = None
    transaction_id: str | None = None
    action: str | None = None
    amount_paise: int | None = None
    details: dict = dataclasses.field(default_factory=dict)


class AuditLogger:
    """Append-only; never updates or deletes (§13.6). Every record chains to
    the previous via SHA256(previous_hash + payload_hash + timestamp) (§13.2).
    If an earlier record is modified, the chain breaks.

    Verifies the chain in application code — don't just trust the DB trigger.
    """

    def __init__(self) -> None:
        self._chain: list[AuditEntry] = []
        self._last_hash: str = _GENESIS_HASH

    def append(
        self,
        *,
        trigger_type: str,
        trigger_event: str | None = None,
        payload: dict,
        case_id: str | None = None,
        obligation_id: str | None = None,
        customer_id: str | None = None,
        transaction_id: str | None = None,
        action: str | None = None,
        amount_paise: int | None = None,
        **extra: object,
    ) -> AuditEntry:
        """Append a new entry to the hash chain."""
        now = clock.now()

        # Hash the payload
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()

        # Build the chain: SHA256(previous_hash + payload_hash + timestamp)
        chain_input = f"{self._last_hash}{payload_hash}{now.isoformat()}"
        current_hash = hashlib.sha256(chain_input.encode()).hexdigest()

        entry = AuditEntry(
            entry_id=f"aud_{uuid.uuid4().hex[:12]}",
            previous_hash=self._last_hash,
            current_hash=current_hash,
            trigger_type=trigger_type,
            trigger_event=trigger_event,
            payload_hash=payload_hash,
            created_at=now,
            case_id=case_id,
            obligation_id=obligation_id,
            customer_id=customer_id,
            transaction_id=transaction_id,
            action=action,
            amount_paise=amount_paise,
            details=dict(extra),
        )

        self._chain.append(entry)
        self._last_hash = current_hash

        logger.debug(
            "Audit entry %s: %s/%s case=%s hash=%s…",
            entry.entry_id,
            trigger_type,
            trigger_event or "-",
            case_id or "-",
            current_hash[:16],
        )
        return entry

    def verify_chain(self) -> list[str]:
        """Verify the integrity of the entire hash chain.

        Returns a list of error messages. Empty list = chain is intact.
        This is application-level verification — don't just trust the
        DB trigger (§13.2).
        """
        errors: list[str] = []

        if not self._chain:
            return errors

        # First entry must chain from genesis
        if self._chain[0].previous_hash != _GENESIS_HASH:
            errors.append(
                f"Entry 0 ({self._chain[0].entry_id}): previous_hash "
                f"is not genesis hash"
            )

        for i, entry in enumerate(self._chain):
            # Verify the hash was computed correctly
            expected_prev = _GENESIS_HASH if i == 0 else self._chain[i - 1].current_hash

            if entry.previous_hash != expected_prev:
                errors.append(
                    f"Entry {i} ({entry.entry_id}): previous_hash mismatch "
                    f"(expected {expected_prev[:16]}…, got {entry.previous_hash[:16]}…)"
                )

            # Re-compute the hash and verify
            chain_input = (
                f"{entry.previous_hash}"
                f"{entry.payload_hash}"
                f"{entry.created_at.isoformat()}"
            )
            recomputed = hashlib.sha256(chain_input.encode()).hexdigest()

            if entry.current_hash != recomputed:
                errors.append(
                    f"Entry {i} ({entry.entry_id}): hash TAMPERED "
                    f"(expected {recomputed[:16]}…, got {entry.current_hash[:16]}…)"
                )

        if errors:
            logger.error("Audit chain verification FAILED: %d errors", len(errors))
        else:
            logger.info(
                "Audit chain verification PASSED (%d entries)", len(self._chain)
            )

        return errors

    @property
    def chain_length(self) -> int:
        return len(self._chain)

    @property
    def last_hash(self) -> str:
        return self._last_hash

    def entries_for_case(self, case_id: str) -> list[AuditEntry]:
        """Get all audit entries for a specific case."""
        return [e for e in self._chain if e.case_id == case_id]
