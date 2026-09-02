"""Behavioural anomaly detection (§14.6, §12.5).

Flags agent-behaviour anomalies such as:
  - Sudden SMS ratio jump (runaway notification loop)
  - Classification distribution shift (model drift)
  - Payment link conversion drop (policy/model issue)
  - Action rate spike (blast-radius concern)

Complements the blast-radius guard in app/policy/blast_radius.py.
"""

from __future__ import annotations

import dataclasses
import logging

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Anomaly:
    """A single detected behavioural anomaly."""

    metric: str
    observed: float
    expected: float
    deviation_pct: float
    severity: str  # LOW / MEDIUM / HIGH / CRITICAL
    description: str


class AnomalyDetector:
    """Flags agent-behaviour anomalies by comparing observed distributions
    against historical baselines (§14.6, §12.5).

    Does not auto-retrain. Just demonstrates detection.
    """

    def __init__(
        self,
        baselines: dict[str, dict] | None = None,
        deviation_threshold: float = 0.50,
    ) -> None:
        # metric_name → {"expected": float, "tolerance": float}
        self._baselines = baselines or self._default_baselines()
        self._threshold = deviation_threshold

    def detect(self, signals: dict[str, float]) -> list[Anomaly]:
        """Detect anomalies by comparing signals against baselines.

        Args:
            signals: Current metric values. Keys must match baseline keys.
                Example: {"sms_ratio": 0.65, "plink_conversion": 0.09, ...}

        Returns:
            List of detected anomalies (may be empty if all is normal).
        """
        anomalies: list[Anomaly] = []

        for metric, observed in signals.items():
            baseline = self._baselines.get(metric)
            if baseline is None:
                continue

            expected = baseline["expected"]
            tolerance = baseline.get("tolerance", self._threshold)

            if expected == 0:
                if observed > 0:
                    deviation_pct = 1.0
                else:
                    continue
            else:
                deviation_pct = abs(observed - expected) / abs(expected)

            if deviation_pct > tolerance:
                severity = self._severity(deviation_pct, tolerance)
                anomaly = Anomaly(
                    metric=metric,
                    observed=round(observed, 4),
                    expected=round(expected, 4),
                    deviation_pct=round(deviation_pct * 100, 1),
                    severity=severity,
                    description=self._describe(
                        metric, observed, expected, deviation_pct
                    ),
                )
                anomalies.append(anomaly)
                logger.warning(
                    "ANOMALY [%s]: %s — observed=%.4f, expected=%.4f, "
                    "deviation=%.1f%%",
                    severity,
                    metric,
                    observed,
                    expected,
                    deviation_pct * 100,
                )

        if not anomalies:
            logger.debug("Anomaly detection: all signals within normal range")

        return anomalies

    @staticmethod
    def _severity(deviation_pct: float, tolerance: float) -> str:
        """Map deviation to severity level."""
        ratio = deviation_pct / tolerance
        if ratio > 4.0:
            return "CRITICAL"
        if ratio > 2.5:
            return "HIGH"
        if ratio > 1.5:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _describe(metric: str, observed: float, expected: float, dev: float) -> str:
        direction = "above" if observed > expected else "below"
        return (
            f"{metric} is {dev * 100:.0f}% {direction} baseline "
            f"(observed={observed:.4f}, expected={expected:.4f})"
        )

    @staticmethod
    def _default_baselines() -> dict[str, dict]:
        """Default baselines for common agent metrics (§12.5)."""
        return {
            "sms_ratio": {"expected": 0.25, "tolerance": 0.50},
            "email_ratio": {"expected": 0.20, "tolerance": 0.50},
            "plink_conversion": {"expected": 0.25, "tolerance": 0.50},
            "no_action_ratio": {"expected": 0.30, "tolerance": 0.60},
            "error_rate": {"expected": 0.02, "tolerance": 0.80},
            "policy_block_rate": {"expected": 0.15, "tolerance": 0.60},
            "human_escalation_rate": {"expected": 0.05, "tolerance": 0.80},
        }
