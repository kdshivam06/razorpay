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

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}

    def create(self, experiment: Experiment) -> str:
        if experiment.experiment_id in self._experiments:
            raise ValueError(f"Experiment already exists: {experiment.experiment_id}")
        self._experiments[experiment.experiment_id] = experiment
        return experiment.experiment_id

    def redeem(self, experiment_id: str, metric: str, value: float) -> None:
        experiment = self._get(experiment_id)
        history = experiment.results.setdefault(metric, [])
        if isinstance(history, list):
            history.append(float(value))
        else:
            experiment.results[metric] = [float(history), float(value)]

    def should_stop(self, experiment_id: str) -> bool:
        experiment = self._get(experiment_id)
        latest = {
            metric: values[-1] if isinstance(values, list) else values
            for metric, values in experiment.results.items()
            if values != []
        }
        for metric, limit in experiment.guardrails.items():
            if metric in latest and float(latest[metric]) > float(limit):
                return True
        for metric, condition in experiment.stop_conditions.items():
            if metric not in latest:
                continue
            if isinstance(condition, dict):
                if "max" in condition and float(latest[metric]) > float(condition["max"]):
                    return True
                if "min" in condition and float(latest[metric]) < float(condition["min"]):
                    return True
            elif float(latest[metric]) >= float(condition):
                return True
        return False

    def _get(self, experiment_id: str) -> Experiment:
        try:
            return self._experiments[experiment_id]
        except KeyError as exc:
            raise KeyError(f"Unknown experiment: {experiment_id}") from exc
