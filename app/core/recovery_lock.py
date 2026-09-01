"""Single active recovery path per obligation — the recovery_lock (§3.4)."""

from __future__ import annotations

import threading
from typing import Any


class RecoveryLockError(RuntimeError):
    """Raised when a recovery path cannot acquire or release a lock."""


class RecoveryLock:
    """Thread-safe registry mapping obligation_id -> single recovery holder.

    Only one financial recovery path may be active per obligation. When an
    obligation is RECOVERED, `release_all` cancels every remaining path.
    """

    def __init__(self) -> None:
        self._holders: dict[str, str] = {}
        self._mutex = threading.Lock()

    def acquire(self, obligation_id: str, holder: str = "default") -> bool:
        """Grab the lock for this holder. Returns False if already held."""
        if not obligation_id:
            raise ValueError("obligation_id is required")
        with self._mutex:
            if obligation_id in self._holders:
                return False
            self._holders[obligation_id] = holder
            return True

    def is_held(self, obligation_id: str) -> bool:
        with self._mutex:
            return obligation_id in self._holders

    def holder(self, obligation_id: str) -> str | None:
        with self._mutex:
            return self._holders.get(obligation_id)

    def release(self, obligation_id: str, holder: str | None = None) -> bool:
        """Release the lock. If `holder` is given it must match the current one."""
        with self._mutex:
            current = self._holders.get(obligation_id)
            if current is None:
                return False
            if holder is not None and current != holder:
                raise RecoveryLockError(
                    f"Lock on {obligation_id} is held by {current!r}, not {holder!r}"
                )
            del self._holders[obligation_id]
            return True

    def release_all(self, obligation_id: str) -> list[Any]:
        """Release every recovery path for an obligation (e.g. on RECOVERED).

        Returns the list of cancelled holders so the caller can CANCELLED them.
        """
        with self._mutex:
            holders = self._holders.pop(obligation_id, None)
        return [holders] if holders is not None else []

    def clear(self) -> None:
        with self._mutex:
            self._holders.clear()

    def __len__(self) -> int:
        with self._mutex:
            return len(self._holders)
