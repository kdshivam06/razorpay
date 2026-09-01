"""Agent health monitor — self-monitoring (§14.6, §8.5)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class AgentHealthReport:
    """Aggregate of the agent's behavioural health signals."""

    classification_distribution: dict[str, float]
    action_distribution: dict[str, float]
    anomaly_count: int
    overall_status: str


class AgentMonitor:
    """Self-monitors the agent's behaviour: classification distribution,
    action distribution, and anomaly detection (§14.6)."""

    def report(self) -> AgentHealthReport:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §14.6")

    def record_classification(self, root_cause: str) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §14.6")

    def record_action(self, action: str) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §14.6")
