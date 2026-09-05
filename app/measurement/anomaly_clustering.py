"""Anomaly clustering over decision traces (§12.3).

Group the batch into cohorts — failure family × IST hour × value tier —
and compare each cohort's unresolved/failure rate against the batch
baseline with a two-proportion z-test. Clusters that clear the flag
threshold (z >= min_z, cohort >= min_cohort, cohort rate >= baseline)
are surfaced as anomalies with a one-line fix suggestion.

Detection only: nothing here changes policy or re-trains a model.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from app.audit.decision_trace import DecisionTrace

_IST = ZoneInfo("Asia/Kolkata")

_RESOLVED_OUTCOMES = frozenset({"RECOVERED", "PAID", "SUCCESS", "RESOLVED", "SETTLED"})

_HIGH_VALUE_PAISE = 1_000_000_00  # ₹1L (matches human-review high-value doorstep)

# root_cause -> failure-family bucket used for clustering and fix suggestions.
_FAILURE_FAMILIES: dict[str, str] = {
    "bank_timeout": "BANK_TIMEOUT",
    "gateway_error": "GATEWAY",
    "intl_decline": "INTL_DECLINE",
    "expired_card": "CARD_EXPIRY",
    "risk_block": "RISK_HOLD",
    "dispute": "DISPUTE",
    "dispute_filed": "DISPUTE",
    "mandate_failure": "MANDATE",
    "mandate_revoked_customer": "MANDATE",
    "mandate_revoked_bank": "MANDATE",
    "subscription_pending": "MANDATE",
    "insufficient_funds": "INSUFFICIENT_FUNDS",
    "checkout_abandoned": "ABANDONED_CHECKOUT",
    "partial_payment": "OVERDUE_INVOICE",
    "overdue_invoice": "OVERDUE_INVOICE",
}

# One-line operational fix suggested when a cohort clears the flag rule.
_FIX_SUGGESTIONS: dict[str, str] = {
    "BANK_TIMEOUT": "Move retries out of the 10:00-13:00 IST bank congestion window or fall back to a debit mandate.",
    "GATEWAY": "Route this cohort to a secondary gateway with stale-IPV/attempt retry for resilience.",
    "INTL_DECLINE": "Enable per-transaction 3DS auth and a localized billing descriptor; review raw decline codes.",
    "CARD_EXPIRY": "Ask for an updated payment method before charging; prefer UPI/mandate for this customer.",
    "RISK_HOLD": "Route to the manual review queue - do not auto-retry risk-held charges.",
    "MANDATE": "Re-confirm the e-mandate (OTP + bank signature) and re-register with the paying account.",
    "INSUFFICIENT_FUNDS": "Shift the nudge to payday evening slots and offer split / partial payment.",
    "ABANDONED_CHECKOUT": "Lower friction (UPI vault, saved intent) and nudge within two hours of abandonment.",
    "DISPUTE": "Stop auto-recovery and forward documents to the dispute workflow.",
    "OVERDUE_INVOICE": "Escalate to the MSMED s.16 demand + conciliation ladder for this overdue B2B invoice.",
    "UNKNOWN": "Re-derive the root cause; check the gateway raw failure-code mapping.",
}

_MIN_Z_FOR_FLAG = 2.58  # 99% two-tail equivalent


@dataclasses.dataclass(frozen=True)
class AnomalyCluster:
    """One cohort that clears (or nearly clears) the flag rule."""

    failure_family: str
    hour_ist: int | None
    amount_tier: str
    size: int
    n_failed: int
    failure_rate: float
    baseline_failure_rate: float
    deviation_pp: float
    z_score: float
    flagged: bool
    revenue_at_risk_paise: int
    suggested_fix: str
    severity: str  # HIGH | MEDIUM | WATCH

    def to_dict(self) -> dict[str, object]:
        return {
            "failure_family": self.failure_family,
            "hour_ist": self.hour_ist,
            "amount_tier": self.amount_tier,
            "size": self.size,
            "n_failed": self.n_failed,
            "failure_rate": round(self.failure_rate, 4),
            "baseline_failure_rate": round(self.baseline_failure_rate, 4),
            "deviation_pp": round(self.deviation_pp, 2),
            "z_score": round(self.z_score, 2) if math.isfinite(self.z_score) else None,
            "flagged": self.flagged,
            "revenue_at_risk_paise": self.revenue_at_risk_paise,
            "suggested_fix": self.suggested_fix,
            "severity": self.severity,
        }


@dataclasses.dataclass(frozen=True)
class AnomalyReport:
    """Batch-level summary plus the cohort anomalies ordered by severity."""

    total_traces: int
    n_failed: int
    baseline_failure_rate: float
    flagged_count: int
    window_label: str
    clusters: list[AnomalyCluster]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_traces": self.total_traces,
            "n_failed": self.n_failed,
            "baseline_failure_rate": round(self.baseline_failure_rate, 4),
            "flagged_count": self.flagged_count,
            "window_label": self.window_label,
            "clusters": [c.to_dict() for c in self.clusters],
        }


def failure_family_for(root_cause: str) -> str:
    """Bucket a raw root cause into the clustering failure family."""
    key = str(root_cause or "").strip().lower()
    return _FAILURE_FAMILIES.get(key, "UNKNOWN")


def ist_hour_for(timestamp: datetime | None) -> int | None:
    """UTC timestamp -> IST 0-23 hour (no dependency on machine TZ)."""
    if timestamp is None:
        return None
    ist = timestamp.astimezone(_IST)
    return ist.hour


def _is_failed(trace: DecisionTrace, failure_threshold: float) -> bool:
    """Unresolved outcome, or (no outcome label) high unresolved risk."""
    outcome = str(trace.outcome or "").upper()
    if outcome:
        return outcome not in _RESOLVED_OUTCOMES
    return (1.0 - min(1.0, max(0.0, trace.natural_payment_probability))) >= failure_threshold


def _two_proportion_z(
    p1: float, n1: int, p2: float, n2: int
) -> float:
    """Two-proportion z score for cohort (p1,n1) vs baseline (p2,n2)."""
    if p1 <= p2:
        return 0.0
    if n1 <= 0 or n2 <= 0:
        return math.inf
    total = n1 + n2
    pooled = (p1 * n1 + p2 * n2) / total
    if pooled <= 0.0 or pooled >= 1.0:
        return math.inf
    se = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2))
    return math.inf if se == 0.0 else (p1 - p2) / se


class AnomalyClusterer:
    """Clusters decision traces into cohorts and flags statistical outliers."""

    def __init__(
        self,
        *,
        min_cohort: int = 5,
        min_z: float = _MIN_Z_FOR_FLAG,
        failure_threshold: float = 0.5,
    ) -> None:
        if min_cohort < 1:
            raise ValueError("min_cohort must be >= 1")
        if min_z < 0:
            raise ValueError("min_z must be non-negative")
        self._min_cohort = min_cohort
        self._min_z = min_z
        self._failure_threshold = failure_threshold

    def analyze(self, traces: list[DecisionTrace]) -> AnomalyReport:
        """Cluster the traces and return the anomaly report."""
        total = len(traces)
        if total == 0:
            return AnomalyReport(
                total_traces=0,
                n_failed=0,
                baseline_failure_rate=0.0,
                flagged_count=0,
                window_label="no traces in window",
                clusters=[],
            )

        cohorts: dict[tuple[str, int | None, str], list[DecisionTrace]] = {}
        for trace in traces:
            key = (
                failure_family_for(trace.root_cause),
                ist_hour_for(trace.trigger_timestamp),
                "HIGH" if trace.revenue_at_risk_paise >= _HIGH_VALUE_PAISE else "LOW",
            )
            cohorts.setdefault(key, []).append(trace)

        failed_cohort = {cid: sum(1 for t in ts if _is_failed(t, self._failure_threshold)) for cid, ts in cohorts.items()}
        n_failed_total = sum(failed_cohort.values())
        baseline = n_failed_total / total

        clusters: list[AnomalyCluster] = []
        for (family, hour, tier), members in cohorts.items():
            size = len(members)
            if size < self._min_cohort:
                continue
            n_failed = failed_cohort[(family, hour, tier)]
            rate = n_failed / size
            rest_size = total - size
            rest_failed = n_failed_total - n_failed
            rest_rate = rest_failed / rest_size if rest_size else rate
            z = _two_proportion_z(rate, size, rest_rate, rest_size)
            flagged = rate >= rest_rate and z >= self._min_z
            deviation_pp = (rate - rest_rate) * 100.0
            severity = (
                "HIGH"
                if flagged and deviation_pp >= 15.0
                else "MEDIUM"
                if flagged
                else "WATCH"
            )
            clusters.append(
                AnomalyCluster(
                    failure_family=family,
                    hour_ist=hour,
                    amount_tier=tier,
                    size=size,
                    n_failed=n_failed,
                    failure_rate=rate,
                    baseline_failure_rate=rest_rate,
                    deviation_pp=deviation_pp,
                    z_score=z,
                    flagged=flagged,
                    revenue_at_risk_paise=sum(
                        t.revenue_at_risk_paise for t in members
                    ),
                    suggested_fix=_FIX_SUGGESTIONS.get(family, _FIX_SUGGESTIONS["UNKNOWN"]),
                    severity=severity,
                )
            )

        clusters.sort(
            key=lambda c: (
                0 if c.flagged else 1,
                -abs(c.deviation_pp),
                -c.size,
            )
        )

        return AnomalyReport(
            total_traces=total,
            n_failed=n_failed_total,
            baseline_failure_rate=baseline,
            flagged_count=sum(1 for c in clusters if c.flagged),
            window_label="-".join(
                f"{hour_label:02d}"
                for hour_label in sorted(
                    {c.hour_ist for c in clusters if c.hour_ist is not None}
                )
            )
            or "all-day",
            clusters=clusters,
        )


def analyze_traces(
    traces: list[DecisionTrace],
    *,
    min_cohort: int = 5,
    min_z: float = _MIN_Z_FOR_FLAG,
    failure_threshold: float = 0.5,
) -> AnomalyReport:
    """Convenience wrapper: build a clusterer and run it once."""
    return AnomalyClusterer(
        min_cohort=min_cohort,
        min_z=min_z,
        failure_threshold=failure_threshold,
    ).analyze(traces)