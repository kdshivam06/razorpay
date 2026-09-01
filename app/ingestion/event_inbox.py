"""Event inbox with idempotent dedup by event id (§2.1, §3.5)."""

from __future__ import annotations

import time
from enum import Enum
from threading import RLock


class DedupDecision(str, Enum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"


class EventInbox:
    """Thread-safe in-memory event inbox for the Event Gateway.

    Every event id is remembered for `dedup_window_seconds`. Re-deliveries of
    the same event (replayed webhooks, retries, duplicate pipeline runs) are
    deduplicated and never re-enter the pipeline.
    """

    def __init__(self, dedup_window_seconds: int = 86_400) -> None:
        if dedup_window_seconds <= 0:
            raise ValueError("dedup_window_seconds must be positive")
        self.dedup_window_seconds = dedup_window_seconds
        self._expires_at: dict[str, float] = {}
        self._lock = RLock()

    def insert(self, event_id: str, *, now: float | None = None) -> DedupDecision:
        """Record an event id. Returns NEW or DUPLICATE."""
        if not event_id:
            raise ValueError("event_id is required")
        resolved = now if now is not None else time.time()
        with self._lock:
            self._purge_locked(resolved)
            if event_id in self._expires_at:
                return DedupDecision.DUPLICATE
            self._expires_at[event_id] = resolved + self.dedup_window_seconds
            return DedupDecision.NEW

    def already_seen(self, event_id: str, *, now: float | None = None) -> bool:
        resolved = now if now is not None else time.time()
        with self._lock:
            self._purge_locked(resolved)
            return event_id in self._expires_at

    def purge_expired(self, *, now: float | None = None) -> int:
        """Drop ids older than the dedup window. Returns count removed."""
        resolved = now if now is not None else time.time()
        with self._lock:
            return self._purge_locked(resolved)

    def _purge_locked(self, now: float) -> int:
        expired = [eid for eid, expires in self._expires_at.items() if expires <= now]
        for eid in expired:
            del self._expires_at[eid]
        return len(expired)

    def active_count(self) -> int:
        with self._lock:
            self._purge_locked(time.time())
            return len(self._expires_at)

    def clear(self) -> None:
        with self._lock:
            self._expires_at.clear()
