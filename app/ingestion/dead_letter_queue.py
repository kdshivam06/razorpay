"""Dead letter queue — routes failed/invalid events out of the pipeline."""

from __future__ import annotations

import time
import uuid
from collections import deque
from threading import RLock
from typing import Any


class DeadLetterQueue:
    """Thread-safe FIFO DLQ for rejected, malformed, or failed events.

    Entries are plain dicts; the caller decides what to do with them later
    (inspect, requeue, or discard).
    """

    def __init__(self, max_size: int = 10_000) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.max_size = max_size
        self._entries: deque[dict] = deque()
        self._lock = RLock()

    def push(
        self,
        *,
        reason: str,
        kind: str,
        event_id: str | None = None,
        payload: Any = None,
        headers: dict | None = None,
        retry_count: int = 0,
    ) -> str:
        """Enqueue a failed event. Returns the DLQ message id."""
        message_id = f"dlq_{uuid.uuid4().hex}"
        entry = {
            "message_id": message_id,
            "event_id": event_id,
            "payload": payload,
            "headers": headers or {},
            "reason": reason,
            "kind": kind,
            "retry_count": retry_count,
            "created_at": time.time(),
        }
        with self._lock:
            if len(self._entries) >= self.max_size:
                self._entries.popleft()
            self._entries.append(entry)
        return message_id

    def peek(self) -> dict | None:
        with self._lock:
            return self._entries[0] if self._entries else None

    def pop(self) -> dict | None:
        with self._lock:
            return self._entries.popleft() if self._entries else None

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()