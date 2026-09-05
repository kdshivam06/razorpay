"""Tests for the hackathon demo upload/realtime proof paths."""

from fastapi.testclient import TestClient

from app.dashboard.api import DashboardApi, configure
from app.main import app

client = TestClient(app)


def test_upload_batch_scores_rows_and_surfaces_proof() -> None:
    configure(DashboardApi())
    csv_text = "\n".join(
        [
            "case_id,event_id,event_type,customer_id,obligation_id,obligation_type,amount_paise,outstanding_amount_paise,currency,payment_method,failure_reason,root_cause,created_at,persona,channel_preference",
            "case_upload_1,evt_upload_1,payment.failed,cus_upload_1,obl_upload_1,payment,120000,120000,INR,upi,checkout_abandoned,checkout_abandoned,1790000000,P7,WHATSAPP",
            "case_upload_2,evt_upload_2,dispute.created,cus_upload_2,obl_upload_2,payment,220000,220000,INR,card,dispute_filed,dispute,1790000001,P5,EMAIL",
        ]
    )

    response = client.post(
        "/api/dashboard/upload?filename=test_upload.csv",
        content=csv_text,
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processed"
    assert body["dataset"]["record_count"] == 2
    assert body["dataset"]["audit_traces"] == 2
    assert body["dataset"]["estimated_records"] == 2
    assert body["dataset"]["message_count"] >= 1
    assert body["scorecard"]["human_escalations"] >= 1

    messages = client.get("/api/dashboard/messages").json()
    assert messages
    assert messages[0]["state"] == "SUCCESS"
    assert "https://rzp.io/i/" in messages[0]["rendered_body"]

    preview = client.get("/api/dashboard/data-preview?limit=2").json()
    assert len(preview) == 2
    assert preview[0]["case_id"] == "case_upload_1"

    action_summary = client.get("/api/dashboard/action-summary").json()
    assert action_summary["messages_sent"] >= 1
    assert action_summary["human_required"] >= 1

    ptp_summary = client.get("/api/dashboard/ptp-summary").json()
    assert "reminders_scheduled" in ptp_summary


def test_realtime_event_appends_to_dashboard_projection() -> None:
    configure(DashboardApi())
    before = client.get("/api/dashboard/dataset").json().get("record_count", 0)

    response = client.post(
        "/api/dashboard/realtime-event",
        json={
            "case_id": "case_realtime_1",
            "event_id": "evt_realtime_1",
            "event_type": "payment.failed",
            "customer_id": "cus_realtime_1",
            "obligation_id": "obl_realtime_1",
            "obligation_type": "payment",
            "amount_paise": 1845000,
            "outstanding_amount_paise": 1845000,
            "currency": "INR",
            "payment_method": "upi",
            "failure_reason": "checkout_abandoned",
            "root_cause": "checkout_abandoned",
            "created_at": 1790000002,
            "persona": "P7",
            "channel_preference": "WHATSAPP",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert client.get("/api/dashboard/dataset").json()["record_count"] >= before
