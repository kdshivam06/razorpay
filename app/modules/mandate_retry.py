"""Module E — mandate retry sequencer (§9.5)."""

from __future__ import annotations

import dataclasses
from datetime import datetime


@dataclasses.dataclass(frozen=True)
class RetrySequence:
    """The dynamic retry schedule for a mandate."""

    sub_id: str
    attempts: list[datetime]
    reason: str


class MandateRetryModule:
    """Uses the dynamic timing model instead of fixed retry schedules; considers
    salary-day heuristic, bank maintenance windows, and customer payment
    patterns (§9.5)."""

    def schedule(self, sub_id: str, customer_id: str) -> RetrySequence:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.5")