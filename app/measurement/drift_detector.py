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
        if threshold < 0:
            raise ValueError("threshold must be non-negative")

        delta = current - baseline
        absolute_delta = abs(delta)
        relative_delta = absolute_delta / abs(baseline) if baseline else absolute_delta
        drifted = absolute_delta >= threshold
        if drifted:
            detail = (
                "MODEL / POLICY DRIFT flagged: "
                f"metric moved from {baseline:.4f} to {current:.4f} "
                f"(absolute_delta={absolute_delta:.4f}, relative_delta={relative_delta:.4f}). "
                "Detection only; no automatic retraining."
            )
        else:
            detail = (
                "No drift flagged: "
                f"metric moved from {baseline:.4f} to {current:.4f} "
                f"within threshold {threshold:.4f}."
            )
        return DriftSignal(
            metric="monitored_metric",
            baseline=float(baseline),
            current=float(current),
            drifted=drifted,
            detail=detail,
        )
