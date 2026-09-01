"""Platform-degraded mode — dependency health and fail-closed gates (§2.4)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DependencyStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


@dataclass
class Dependency:
    """Health record for a single platform dependency."""

    name: str
    critical: bool = False
    status: DependencyStatus = DependencyStatus.HEALTHY
    reason: str = ""
    last_checked: float = 0.0


class DependencyHealth:
    """Tracks dependency health and enforces the plan's fail-closed hierarchy.

    - Outbound financial/contact action requires Redis -> FAIL CLOSED if Redis down.
    - Irreversible action requires DB state guarantees -> BLOCK if DB cannot guarantee.
    """

    def __init__(self) -> None:
        self._dependencies: dict[str, Dependency] = {}

    def register(self, name: str, critical: bool = False) -> None:
        if name not in self._dependencies:
            self._dependencies[name] = Dependency(name=name, critical=critical)

    def report(self, name: str, status: DependencyStatus, reason: str = "") -> None:
        dep = self._dependencies.setdefault(name, Dependency(name=name, critical=False))
        dep.status = status
        dep.reason = reason

    def mark_healthy(self, name: str) -> None:
        self.report(name, DependencyStatus.HEALTHY)

    def mark_degraded(self, name: str, reason: str = "") -> None:
        self.report(name, DependencyStatus.DEGRADED, reason)

    def mark_down(self, name: str, reason: str = "") -> None:
        self.report(name, DependencyStatus.DOWN, reason)

    def status(self, name: str) -> DependencyStatus:
        dep = self._dependencies.get(name)
        return dep.status if dep else DependencyStatus.DOWN

    def is_healthy(self, name: str) -> bool:
        return self.status(name) is DependencyStatus.HEALTHY

    def any_critical_down(self) -> bool:
        return any(
            dep.critical and dep.status is DependencyStatus.DOWN
            for dep in self._dependencies.values()
        )

    def fail_closed_reason(self, action_kind: str) -> str | None:
        """Return a blocking reason if a critical dependency is down, else None.

        `action_kind`: "contact" (outbound comms) or "financial" (irreversible).
        """
        down_critical = [
            dep
            for dep in self._dependencies.values()
            if dep.critical and dep.status is DependencyStatus.DOWN
        ]
        if not down_critical:
            return None
        required = {"contact": {"redis"}, "financial": {"database"}}
        blocking = [
            dep for dep in down_critical if dep.name in required.get(action_kind, set())
        ]
        if not blocking:
            return None
        return ", ".join(
            f"{dep.name} is {dep.status.value} ({dep.reason or 'no reason'})"
            for dep in blocking
        )
