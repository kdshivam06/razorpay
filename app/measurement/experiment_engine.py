"""Experiment engine — A/B experiment tracking (§12.2)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Experiment:
    """One A/B/counterfactual experiment (§12.2)."""

    experiment_id: str
    population: str
    control_group: list[str]
    treatment_group: list[str]
    primary_metric: str
    guardrails: dict
    stop_conditions: dict
    results: dict


class ExperimentEngine:
    """Tracks experiments with guardrails and stop conditions (§12.2)."""

    def create(self, experiment: Experiment) -> str:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.2")

    def redeem(self, experiment_id: str, metric: str, value: float) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.2")

    def should_stop(self, experiment_id: str) -> bool:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.2")