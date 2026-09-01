"""Blast-radius protection — global rate anomaly detection (§7.5)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action


@dataclasses.dataclass(frozen=True)
class BlastRadiusStatus:
    """Status of global rate limits and merchant-level anomaly detection."""

    breaches: tuple[str, ...]
    anomalous: bool
    circuit_open: bool


class BlastRadiusGuard:
    """Guards against an ML model going haywire at the fleet level (§7.5):

      sms_per_minute | sms_per_customer_day | payment_links_per_case |
      voice_calls_per_customer_day | autonomous_amount_limit
    """

    def check_global_rate(self, action: Action) -> BlastRadiusStatus:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.5")

    def detect_anomaly(
        self, current_ratio: float, baseline_ratio: float, threshold: float
    ) -> bool:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.5")

    def open_circuit(self) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.5")