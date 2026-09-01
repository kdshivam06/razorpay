"""Human-in-the-loop queue (§8.6)."""

from __future__ import annotations

import dataclasses

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


class HumanTaskQueue:
    """Human UI shows the full §13.3 decision trace with APPROVE/REJECT."""

    def enqueue(self, task: HumanTask) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.6")

    def next_highest_priority(self) -> HumanTask | None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.6")

    def approve(self, task_id: str) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.6")

    def reject(self, task_id: str, reason: str) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.6")


def priority_for(case_id: str) -> int:
    raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.6")