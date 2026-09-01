"""Model/policy drift detection (§12.5)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class DriftSignal:
    """A detected behaviour drift. Do NOT auto-retrain — just detect (§12.5)."""

    metric: str
    baseline: float
    current: float
    drifted: bool
    detail: str


class DriftDetector:
    """Monitors behaviour changes, e.g. payment-link conversion 25%→9%.

    Flags MODEL / POLICY DRIFT; never auto-retrains (§12.5)."""

    def check(self, baseline: float, current: float, threshold: float) -> DriftSignal:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.5")