"""Test suite: app wiring — routers, static assets, /health endpoint (Track D steps 3–4)."""

import pytest
from fastapi.testclient import TestClient

from app.audit.decision_trace import DecisionTracer
from app.contracts import Action, CandidateAction
from app.dashboard.api import DashboardApi, configure
from app.main import app

client = TestClient(app)


@pytest.fixture
def seeded_dashboard():
    tr = DecisionTracer()
    for i in range(4):
        tr.build(
            case_id=f"case_{i}",
            trigger_event="test",
            state="RISK_ASSESSED",
            root_cause="insufficient_funds_low",
            natural_payment_probability=0.3,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=100000,
            candidate_actions=[
                CandidateAction(
                    action=Action.SEND_SMS,
                    expected_recovery_paise=100000,
                    communication_cost_paise=100,
                    operational_cost_paise=0,
                    risk_penalty_paise=0,
                    cx_penalty_paise=0,
                    economic_score=0.3,
                )
            ],
            selected_action=Action.SEND_SMS,
            selected_economic_score=0.3,
            policy_checks_passed=[],
            policy_checks_failed=[],
            policy_gate_result="APPROVED",
            execution_result="SUCCESS",
        )
    configure(DashboardApi(tracer=tr))
    yield


def test_static_pages_are_served():
    for path in (
        "/static/index.html",
        "/static/red_team.html",
        "/static/dashboard.js",
        "/static/dashboard.css",
    ):
        assert client.get(path).status_code == 200


def test_dashboard_waterfall_endpoint(seeded_dashboard):
    resp = client.get("/api/dashboard/waterfall")
    assert resp.status_code == 200
    body = resp.json()
    assert body["revenue_at_risk_paise"] == 400_000
    assert (
        body["incremental_recovery_paise"]
        == body["gross_recovery_opportunity_paise"]
        - body["expected_natural_recovery_paise"]
    )
    assert (
        body["incremental_net_recovery_paise"]
        == body["incremental_recovery_paise"] - body["communication_cost_paise"]
    )


def test_dashboard_panels_are_served(seeded_dashboard):
    assert client.get("/api/dashboard/scorecard").status_code == 200
    assert client.get("/api/dashboard/uplift_segments").status_code == 200
    assert client.get("/api/dashboard/contacts_avoided").status_code == 200
    assert client.get("/api/dashboard/exception_queue").status_code == 200


def test_red_team_endpoint_is_live():
    resp = client.post("/api/red-team/attack/invalid_signature", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["detected"] is True
    assert body["blocked"] is True


def test_red_team_attacks_listed():
    resp = client.get("/api/red-team/attacks")
    assert resp.status_code == 200
    assert "invalid_signature" in resp.json()


def test_health_endpoint_returns_liveness_and_checks():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"healthy", "degraded"}
    assert body["version"] == app.version
    assert body["uptime_seconds"] >= 0
    assert "postgres" in body["checks"]
    assert "redis" in body["checks"]
    for check in body["checks"].values():
        assert isinstance(check["ok"], bool)
        assert isinstance(check["detail"], str)
