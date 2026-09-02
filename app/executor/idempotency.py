"""Idempotency key management (§16.1, §8.3).

Ensures duplicate pipeline runs or API calls never produce duplicate
external effects (§16.1, §13.5).
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid


@dataclasses.dataclass(frozen=True)
class IdempotencyRecord:
    """One idempotency key usage serving a single logical request."""

    key: str
    scope: str
    hash: str
    state: str


class IdempotencyManager:
    """Ensures duplicate pipeline runs or API calls never produce duplicate
    external effects (§16.1, §13.5).
    """

    def __init__(self) -> None:
        # In-memory mock: key -> IdempotencyRecord
        self._store: dict[str, IdempotencyRecord] = {}

    def new_key(self, scope: str, payload_hash: str = "") -> str:
        """Generate a new idempotency key for a given scope."""
        key = f"idem_{scope}_{uuid.uuid4().hex}"
        self._store[key] = IdempotencyRecord(
            key=key,
            scope=scope,
            hash=payload_hash,
            state="CREATED",
        )
        return key

    def get_or_create(self, scope: str, payload: dict) -> tuple[str, bool]:
        """Get existing key for payload, or create a new one.
        Returns (key, is_new).
        """
        import json

        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

        # Check if we already have this payload hash in this scope
        for record in self._store.values():
            if record.scope == scope and record.hash == payload_hash:
                return (record.key, False)

        return (self.new_key(scope, payload_hash), True)

    def check(self, key: str) -> bool:
        """Check if an idempotency key is already used (i.e. successfully completed)."""
        record = self._store.get(key)
        if not record:
            return False
        return record.state == "USED"

    def mark_used(self, key: str) -> None:
        """Mark an idempotency key as successfully used."""
        record = self._store.get(key)
        if record:
            self._store[key] = dataclasses.replace(record, state="USED")

    def release(self, key: str) -> None:
        """Release a key (e.g. if the API call failed and can be safely retried)."""
        record = self._store.get(key)
        if record and record.state != "USED":
            self._store[key] = dataclasses.replace(record, state="RELEASED")
