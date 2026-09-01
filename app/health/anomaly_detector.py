"""Behavioral anomaly detection (§14.6, §12.5)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Anomaly:
    """A single detected behavioural anomaly."""

    metric: str
    observed: float
    expected: float
    severity: str


class AnomalyDetector:
    """Flags agent-behaviour anomalies such as a sudden SMS/action ratio jump,
    complementing the blast-radius guard (§14.6)."""

    def detect(self, signals: dict[str, dict]) -> list[Anomaly]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §14.6")