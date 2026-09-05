"""E.11 anomaly clustering — two-proportion z-test vs batch baseline."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.audit.decision_trace import DecisionTrace
from app.measurement.anomaly_clustering import (
    AnomalyClusterer,
    analyze_traces,
    failure_family_for,
    ist_hour_for,
)

_UTC = timezone.utc


def _trace(
    case_id: str,
    *,
    root_cause: str = "insufficient_funds",
    hour: int = 10,
    amount: int = 500_000_00,
    natural: float = 0.5,
    outcome: str | None = None,
) -> DecisionTrace:
    return DecisionTrace(
        case_id=case_id,
        trigger_event="payment.failed",
        trigger_timestamp=datetime(2026, 6, 10, hour=hour, tzinfo=_UTC),
        state="RISK_ASSESSED",
        root_cause=root_cause,
        natural_payment_probability=natural,
        uplift_segment="PERSUADABLE",
        revenue_at_risk_paise=amount,
        candidate_actions=[],
        selected_action=None,
        selected_economic_score=0.0,
        selection_reasoning="",
        timing_rationale="",
        rejected_actions={},
        policy_checks_passed=[],
        policy_checks_failed=[],
        policy_gate_result="APPROVED",
        outcome=outcome,
    )


def _resolved(case_id: str, **kw: object) -> DecisionTrace:
    kw["outcome"] = "RECOVERED"
    return _trace(case_id, **kw)


class TestFamiliesAndHours:
    def test_known_failure_family_mapping(self) -> None:
        assert failure_family_for("bank_timeout") == "BANK_TIMEOUT"
        assert failure_family_for("gateway_error") == "GATEWAY"
        assert failure_family_for("intl_decline") == "INTL_DECLINE"
        assert failure_family_for("expired_card") == "CARD_EXPIRY"
        assert failure_family_for("mandate_failure") == "MANDATE"
        assert failure_family_for("overdue_invoice") == "OVERDUE_INVOICE"
        assert failure_family_for("Bank_Timeout") == "BANK_TIMEOUT"

    def test_unknown_family_buckets_to_unknown(self) -> None:
        assert failure_family_for("mystery_code") == "UNKNOWN"
        assert failure_family_for("") == "UNKNOWN"

    def test_ist_hour_conversion(self) -> None:
        utc_dawn = datetime(2026, 6, 10, 5, 30, tzinfo=_UTC)
        assert ist_hour_for(utc_dawn) == 11

    def test_ist_hour_none(self) -> None:
        assert ist_hour_for(None) is None


class TestClusteringFlags:
    def test_clean_batch_has_no_flags(self) -> None:
        traces = [
            _resolved(f"c{i:02d}", root_cause="bank_timeout", hour=12)
            for i in range(40)
        ]
        report = analyze_traces(traces)
        assert report.flagged_count == 0
        assert report.baseline_failure_rate == 0.0
        assert report.total_traces == 40

    def test_planted_cluster_is_flagged_high(self) -> None:
        traces = [
            _resolved(f"bg{i:02d}", root_cause="gateway_error", hour=9)
            for i in range(40)
        ]
        planted = [
            _trace(f"p{i:02d}", root_cause="bank_timeout", hour=12, outcome="PENDING")
            for i in range(10)
        ]
        report = analyze_traces(traces + planted)

        flags = [c for c in report.clusters if c.flagged]
        bank = next(
            c
            for c in report.clusters
            if c.failure_family == "BANK_TIMEOUT"
        )
        assert len(flags) == 1
        assert flags[0].failure_family == "BANK_TIMEOUT"
        assert flags[0].severity == "HIGH"
        assert flags[0].failure_rate == 1.0
        assert flags[0].z_score >= 2.58
        assert flags[0].deviation_pp > 50
        assert flags[0].size == 10
        assert flags[0].n_failed == 10
        assert bank.suggested_fix.startswith("Move retries out of")
        assert "bank" in bank.suggested_fix

    def test_clean_cohort_not_flagged_when_baseline_worse(self) -> None:
        traces = [
            _trace(f"bg{i:02d}", root_cause="gateway_error", hour=9, outcome="PENDING")
            for i in range(30)
        ]
        ok = [
            _resolved(f"ok{i:02d}", root_cause="bank_timeout", hour=12)
            for i in range(10)
        ]
        report = analyze_traces(traces + ok)
        asserting = [
            c for c in report.clusters if c.failure_family == "BANK_TIMEOUT"
        ]
        assert asserting
        assert all(not c.flagged for c in asserting)

    def test_min_cohort_excludes_small_clusters(self) -> None:
        traces = [_resolved(f"bg{i:02d}") for i in range(30)]
        traces += [
            _trace(f"tiny{i}", root_cause="bank_timeout", hour=12, outcome="PENDING")
            for i in range(3)
        ]
        report = analyze_traces(traces, min_cohort=5)
        assert all(c.failure_family != "BANK_TIMEOUT" for c in report.clusters)

    def test_min_z_tightens_or_relaxes_flags(self) -> None:
        traces = [_resolved(f"bg{i:02d}") for i in range(30)]
        planted = [
            _trace(f"p{i:02d}", root_cause="intl_decline", hour=18, outcome="PENDING")
            for i in range(8)
        ]
        loose = analyze_traces(traces + planted, min_z=0.0)
        strict = analyze_traces(traces + planted, min_z=99.0)
        assert loose.flagged_count == 1
        assert strict.flagged_count == 0

    def test_amount_tier_splits_high_and_low(self) -> None:
        traced = [
            _trace(
                f"h{i:02d}",
                root_cause="bank_timeout",
                hour=12,
                amount=20_000_000_00,
                outcome="PENDING",
            )
            for i in range(6)
        ]
        traced += [
            _trace(
                f"l{i:02d}",
                root_cause="bank_timeout",
                hour=12,
                amount=200_000_00,
                outcome="PENDING",
            )
            for i in range(6)
        ]
        report = analyze_traces(traced)
        tiers = {c.amount_tier for c in report.clusters if c.failure_family == "BANK_TIMEOUT"}
        assert tiers == {"HIGH", "LOW"}

    def test_revenue_accumulates_per_cohort(self) -> None:
        traced = [
            _trace(f"a{i}", root_cause="risk_block", hour=8, amount=1_000_000_00, outcome="PENDING")
            for i in range(7)
        ]
        report = analyze_traces(traced)
        cohort = next(c for c in report.clusters if c.failure_family == "RISK_HOLD")
        assert cohort.revenue_at_risk_paise == 7_000_000_00

    def test_suggestion_for_risk_hold_is_manual(self) -> None:
        traced = [
            _trace(f"a{i}", root_cause="risk_block", hour=8, outcome="PENDING")
            for i in range(7)
        ]
        report = analyze_traces(traced)
        cohort = next(c for c in report.clusters if c.failure_family == "RISK_HOLD")
        assert "manual review" in cohort.suggested_fix

    def test_empty_traces(self) -> None:
        report = analyze_traces([])
        assert report.total_traces == 0
        assert report.flagged_count == 0
        assert report.clusters == []

    def test_validates_parameters(self) -> None:
        with pytest.raises(ValueError):
            AnomalyClusterer(min_cohort=0)
        with pytest.raises(ValueError):
            AnomalyClusterer(min_z=-1.0)

    def test_natural_probability_proxy_when_unlabelled(self) -> None:
        background = [
            _resolved(f"bg{i:02d}", root_cause="gateway_error", hour=15)
            for i in range(40)
        ]
        at_risk = [
            _trace(f"u{i:02d}", root_cause="mandate_failure", hour=15, natural=0.1)
            for i in range(8)
        ]
        safe = [
            _trace(f"s{i:02d}", root_cause="mandate_failure", hour=15, natural=0.9)
            for i in range(40)
        ]
        report = analyze_traces(background + at_risk + safe, failure_threshold=0.5)
        mandate = next(
            c for c in report.clusters if c.failure_family == "MANDATE"
        )
        assert mandate.n_failed == 8
        assert mandate.flagged


class TestAnomalyEndpoint:
    def test_anomalies_endpoint_against_demo(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        client.post("/api/dashboard/load-synthetic")
        resp = client.get("/api/dashboard/anomalies")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["clusters"], list)
        assert isinstance(data["flagged_count"], int)
        assert data["total_traces"] > 0
        for cluster in data["clusters"]:
            assert cluster["severity"] in {"HIGH", "MEDIUM", "WATCH"}
            assert isinstance(cluster["failure_family"], str)
            if cluster["flagged"]:
                assert cluster["severity"] in {"HIGH", "MEDIUM"}
        flagged = sum(1 for c in data["clusters"] if c["flagged"])
        assert flagged == data["flagged_count"]
        assert "Detection only" in data["note"]