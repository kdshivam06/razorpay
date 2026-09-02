"""Agent health monitor — self-monitoring (§14.6, §8.5).

Tracks the agent's behavioural health signals:
  - Classification distribution (sudden shift = drift)
  - Action distribution (sudden SMS spike = possible runaway)
  - Error rates and circuit breaker state
  - Cases processed per minute
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class AgentHealthReport:
    """Aggregate of the agent's behavioural health signals."""

    classification_distribution: dict[str, float]
    action_distribution: dict[str, float]
    error_rate: float
    anomaly_count: int
    cases_processed: int
    uptime_seconds: float
    overall_status: str  # HEALTHY / DEGRADED / CRITICAL


class AgentMonitor:
    """Self-monitors the agent's behaviour: classification distribution,
    action distribution, error rates, and throughput (§14.6).

    Feeds into the anomaly detector and blast-radius guard.
    """

    def __init__(self) -> None:
        self._start_time = time.time()
        self._classifications: dict[str, int] = defaultdict(int)
        self._actions: dict[str, int] = defaultdict(int)
        self._errors: int = 0
        self._total: int = 0
        self._anomalies: int = 0

    def record_classification(self, root_cause: str) -> None:
        """Record a root-cause classification event."""
        self._classifications[root_cause] += 1
        self._total += 1

    def record_action(self, action: str) -> None:
        """Record an action execution event."""
        self._actions[action] += 1

    def record_error(self) -> None:
        """Record an execution error."""
        self._errors += 1

    def record_anomaly(self) -> None:
        """Record a detected anomaly."""
        self._anomalies += 1

    def report(self) -> AgentHealthReport:
        """Generate a health report snapshot."""
        total_cls = sum(self._classifications.values()) or 1
        cls_dist = {
            k: round(v / total_cls, 3) for k, v in self._classifications.items()
        }

        total_act = sum(self._actions.values()) or 1
        act_dist = {k: round(v / total_act, 3) for k, v in self._actions.items()}

        error_rate = self._errors / max(self._total, 1)
        uptime = time.time() - self._start_time

        # Determine overall status
        if error_rate > 0.20 or self._anomalies > 5:
            status = "CRITICAL"
        elif error_rate > 0.05 or self._anomalies > 2:
            status = "DEGRADED"
        else:
            status = "HEALTHY"

        report = AgentHealthReport(
            classification_distribution=cls_dist,
            action_distribution=act_dist,
            error_rate=round(error_rate, 3),
            anomaly_count=self._anomalies,
            cases_processed=self._total,
            uptime_seconds=round(uptime, 1),
            overall_status=status,
        )

        logger.info(
            "Agent health: %s (cases=%d, errors=%d, anomalies=%d, uptime=%.0fs)",
            status,
            self._total,
            self._errors,
            self._anomalies,
            uptime,
        )
        return report
