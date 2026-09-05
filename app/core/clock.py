"""Demo-overridable wall clock (§E.12).

Every time-sensitive path reads time from this clock. In production it
behaves exactly like ``datetime.now(timezone.utc)``; in the E.12 demo the
Ops Console can pin or advance it so "today" moves forward deterministically
(MSMED §16 accrual, demand-notice stages, cooldowns, PTP dates…).

No persisted state: the override lives only for the process lifetime.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone


class Clock:
    """A UTC wall clock that can be pinned/advanced for demos."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._override: datetime | None = None

    def set(self, value: datetime | None) -> None:
        """Pin the clock to a fixed UTC instant. ``None`` restores real time.

        Naive datetimes are interpreted as UTC.
        """
        if value is not None:
            if not isinstance(value, datetime):
                raise TypeError("clock override must be a datetime or None")
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
        with self._lock:
            self._override = value

    def advance(self, **delta: float) -> datetime:
        """Move the pin forward by ``timedelta`` kwargs (hours=…, days=…).

        When the clock is not pinned, "now + delta" becomes the new pin so
        the demo can start from real time and step forward.
        """
        with self._lock:
            base = (
                self._override
                if self._override is not None
                else datetime.now(timezone.utc)
            )
            self._override = base + timedelta(**delta)
            return self._override

    def reset(self) -> None:
        """Return to real wall-clock time."""
        with self._lock:
            self._override = None

    def is_overridden(self) -> bool:
        with self._lock:
            return self._override is not None

    def now(self) -> datetime:
        """Current UTC instant (pinned or real)."""
        with self._lock:
            if self._override is not None:
                return self._override
        return datetime.now(timezone.utc)

    def utcnow(self) -> datetime:
        return self.now()

    def today(self) -> datetime.date:
        """UTC calendar date for the current (pinned or real) instant."""
        return self.now().date()


def clock_now() -> datetime:
    """Functional alias so call sites read consistently."""
    return clock.now()


def clock_today() -> datetime.date:
    return clock.today()


clock = Clock()