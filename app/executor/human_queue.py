"""Human-in-the-loop queue (§8.6).

Human UI shows the full decision trace with APPROVE/REJECT.
"""

from __future__ import annotations

import dataclasses
import heapq
import time
from typing import Any

from app.contracts import Action, HumanPriority


@dataclasses.dataclass(frozen=True)
class HumanTask:
    """A queued human approval task with its SLA."""

    task_id: str
    case_id: str
    priority: HumanPriority
    action: Action
    proposed_reasoning: str
    sla_minutes: int
    created_at: float = dataclasses.field(default_factory=time.time)

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, HumanTask):
            return NotImplemented
        # Lower enum value is higher priority (P0 < P1 < P2 < P3 < P4)
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at


class HumanTaskQueue:
    """Human UI shows the full §13.3 decision trace with APPROVE/REJECT."""

    def __init__(self) -> None:
        # Min-heap of tasks (sorted by priority, then creation time)
        self._queue: list[HumanTask] = []
        # Store tasks by ID for easy lookup/removal
        self._tasks: dict[str, HumanTask] = {}
        self._processed: set[str] = set()

    def enqueue(self, task: HumanTask) -> None:
        """Add a task to the priority queue."""
        if task.task_id in self._tasks or task.task_id in self._processed:
            return
        heapq.heappush(self._queue, task)
        self._tasks[task.task_id] = task

    def next_highest_priority(self) -> HumanTask | None:
        """Get the highest priority pending task without removing it."""
        while self._queue:
            task = self._queue[0]
            if task.task_id in self._processed:
                heapq.heappop(self._queue)
                continue
            return task
        return None

    def approve(self, task_id: str) -> None:
        """Mark a task as approved."""
        if task_id in self._tasks:
            self._processed.add(task_id)

    def reject(self, task_id: str, reason: str) -> None:
        """Mark a task as rejected, with reasoning."""
        if task_id in self._tasks:
            self._processed.add(task_id)


def priority_for(
    case_id: str,
    amount_paise: int = 0,
    fraud_risk_high: bool = False,
    disputed: bool = False,
    low_confidence: bool = False,
    chronic_failure: bool = False,
) -> tuple[HumanPriority, int]:
    """Determine the priority and SLA for a human review task (§8.6).

    | Priority | Condition | SLA |
    |---|---|---|
    | **P0** | Fraud / dispute | 15 min |
    | **P1** | High-value transaction (>₹1L) | 1 hour |
    | **P2** | Low-confidence classification | 4 hours |
    | **P3** | Chronic failed recovery | 24 hours |
    | **P4** | Ordinary exception | 48 hours |

    Returns (priority, sla_minutes).
    """
    if fraud_risk_high or disputed:
        return HumanPriority.P0, 15
    if amount_paise > 10000000:  # > ₹1,00,000
        return HumanPriority.P1, 60
    if low_confidence:
        return HumanPriority.P2, 240
    if chronic_failure:
        return HumanPriority.P3, 1440
    return HumanPriority.P4, 2880
