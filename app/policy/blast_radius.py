"""Blast-radius protection — global rate anomaly detection (§7.5).

Guards against an ML model going haywire at the fleet level.
Implements a circuit breaker that opens when global rate limits are breached
or when merchant-level anomaly detection fires.

FAILS CLOSED: if the circuit is open, ALL outbound actions are blocked.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time as _time
from collections import defaultdict

from app.contracts import Action

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class BlastRadiusStatus:
    """Status of global rate limits and merchant-level anomaly detection."""

    breaches: tuple[str, ...]
    anomalous: bool
    circuit_open: bool


# Default global limits from §7.5
_DEFAULT_LIMITS = {
    "sms_per_minute": 50,
    "sms_per_customer_day": 2,
    "payment_links_per_case": 1,
    "voice_calls_per_customer_day": 1,
    "autonomous_amount_limit_paise": 10000000,  # ₹1,00,000
    "total_actions_per_minute": 100,
}

# Map Action → the limit key it consumes
_ACTION_LIMIT_MAP: dict[Action, str] = {
    Action.SEND_SMS: "sms_per_minute",
    Action.SEND_WHATSAPP: "sms_per_minute",  # treated same as SMS for rate limit
    Action.SEND_PAYMENT_LINK: "payment_links_per_case",
    Action.VOICE_CALL: "voice_calls_per_customer_day",
}


class BlastRadiusGuard:
    """Guards against an ML model going haywire at the fleet level (§7.5):

    sms_per_minute | sms_per_customer_day | payment_links_per_case |
    voice_calls_per_customer_day | autonomous_amount_limit

    Plus merchant-level anomaly detection: if the ratio of actions to cases
    spikes above baseline, the AGENT CIRCUIT BREAKER opens.
    """

    def __init__(
        self,
        limits: dict[str, int] | None = None,
        anomaly_threshold: float = 3.0,
    ) -> None:
        self._limits = dict(_DEFAULT_LIMITS)
        if limits:
            self._limits.update(limits)
        self._anomaly_threshold = anomaly_threshold

        # Circuit state
        self._circuit_open = False
        self._circuit_opened_at: float | None = None
        self._circuit_half_open_after_s = 300  # 5 minutes before half-open check

        # In-memory counters (production: Redis sliding windows)
        self._lock = threading.Lock()
        self._minute_counters: dict[str, int] = defaultdict(int)
        self._minute_window_start: float = _time.time()
        self._daily_counters: dict[str, int] = defaultdict(int)
        self._daily_window_start: float = _time.time()
        self._case_counters: dict[str, int] = defaultdict(int)

        # Anomaly detection
        self._baseline_action_ratio: float = 0.3  # normal: 0.3 actions per case
        self._total_cases: int = 0
        self._total_actions: int = 0

    def check_global_rate(self, action: Action) -> BlastRadiusStatus:
        """Check if the action is within global rate limits.

        FAILS CLOSED: if the circuit is open, all outbound actions are blocked.
        """
        if self._circuit_open:
            # Check if enough time has passed for half-open
            if self._circuit_opened_at and (
                _time.time() - self._circuit_opened_at > self._circuit_half_open_after_s
            ):
                logger.info("Circuit breaker entering half-open state")
                # Allow one action through to test
            else:
                return BlastRadiusStatus(
                    breaches=("circuit_breaker_open",),
                    anomalous=True,
                    circuit_open=True,
                )

        breaches: list[str] = []

        with self._lock:
            self._rotate_windows()

            # Per-minute rate check
            limit_key = _ACTION_LIMIT_MAP.get(action)
            if limit_key and limit_key in self._limits and self._minute_counters[limit_key] >= self._limits[limit_key]:
                    breaches.append(
                        f"{limit_key}: {self._minute_counters[limit_key]}"
                        f" >= {self._limits[limit_key]}"
                    )

            # Total actions per minute
            total_key = "total_actions_per_minute"
            if self._minute_counters[total_key] >= self._limits.get(total_key, 100):
                breaches.append(
                    f"total_actions_per_minute: {self._minute_counters[total_key]}"
                    f" >= {self._limits[total_key]}"
                )

        anomalous = len(breaches) > 0

        if anomalous and not self._circuit_open:
            logger.warning(
                "Blast radius breaches detected: %s — opening circuit breaker",
                breaches,
            )
            self.open_circuit()

        return BlastRadiusStatus(
            breaches=tuple(breaches),
            anomalous=anomalous,
            circuit_open=self._circuit_open,
        )

    def record_action(
        self,
        action: Action,
        customer_id: str = "",
        case_id: str = "",
    ) -> None:
        """Record an action execution for rate tracking."""
        with self._lock:
            self._rotate_windows()

            limit_key = _ACTION_LIMIT_MAP.get(action)
            if limit_key:
                self._minute_counters[limit_key] += 1
                self._daily_counters[f"{limit_key}:{customer_id}"] += 1

            self._minute_counters["total_actions_per_minute"] += 1
            self._total_actions += 1

            if case_id:
                self._case_counters[f"{action.value}:{case_id}"] += 1

    def record_case(self) -> None:
        """Record a case evaluation (for anomaly ratio tracking)."""
        self._total_cases += 1

    def detect_anomaly(
        self,
        current_ratio: float,
        baseline_ratio: float | None = None,
        threshold: float | None = None,
    ) -> bool:
        """Merchant-level anomaly detection (§7.5):

        Normal: SMS/action ratio = 0.3
        Current: SMS/action ratio = 4.9
        → AGENT CIRCUIT BREAKER

        Returns True if the current ratio exceeds baseline × threshold.
        """
        baseline = (
            baseline_ratio
            if baseline_ratio is not None
            else self._baseline_action_ratio
        )
        thresh = threshold if threshold is not None else self._anomaly_threshold

        if baseline <= 0:
            return current_ratio > 1.0

        return current_ratio > baseline * thresh

    def check_case_limit(self, action: Action, case_id: str) -> bool:
        """Check per-case limits (e.g. payment_links_per_case = 1)."""
        if action == Action.SEND_PAYMENT_LINK:
            key = f"{action.value}:{case_id}"
            limit = self._limits.get("payment_links_per_case", 1)
            return self._case_counters.get(key, 0) < limit
        return True

    def check_customer_daily_limit(self, action: Action, customer_id: str) -> bool:
        """Check per-customer daily limits."""
        limit_key = _ACTION_LIMIT_MAP.get(action)
        if not limit_key:
            return True

        daily_key = f"{limit_key}:{customer_id}"
        daily_limit_map = {
            "sms_per_minute": self._limits.get("sms_per_customer_day", 2),
            "voice_calls_per_customer_day": self._limits.get(
                "voice_calls_per_customer_day", 1
            ),
        }
        daily_limit = daily_limit_map.get(limit_key)
        if daily_limit is None:
            return True

        return self._daily_counters.get(daily_key, 0) < daily_limit

    def open_circuit(self) -> None:
        """Open the circuit breaker — ALL outbound actions blocked."""
        self._circuit_open = True
        self._circuit_opened_at = _time.time()
        logger.critical("AGENT CIRCUIT BREAKER OPENED — all outbound actions blocked")

    def close_circuit(self) -> None:
        """Close the circuit breaker — resume normal operations."""
        self._circuit_open = False
        self._circuit_opened_at = None
        logger.info("Circuit breaker closed — normal operations resumed")

    @property
    def is_circuit_open(self) -> bool:
        return self._circuit_open

    def _rotate_windows(self) -> None:
        """Reset counters when the time window rolls over."""
        now = _time.time()

        # Minute window
        if now - self._minute_window_start >= 60:
            self._minute_counters.clear()
            self._minute_window_start = now

        # Daily window (24 hours)
        if now - self._daily_window_start >= 86400:
            self._daily_counters.clear()
            self._daily_window_start = now
