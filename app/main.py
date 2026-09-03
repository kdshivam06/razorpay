"""FastAPI entry point — wiring config, DB session, and the ingestion router."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db.session import get_db  # noqa: F401  (exposes the session dependency)
from app.dashboard.api import router as dashboard_router
from app.dashboard.red_team_api import router as red_team_router
from app.ingestion.webhook_handler import WebhookHandler, WebhookResultStatus

settings = get_settings()

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger("recoveryos")

app = FastAPI(
    title="RecoveryOS — AI Revenue Recovery Optimizer",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Ingestion router (Event Gateway)
# ---------------------------------------------------------------------------

_webhook_handler = WebhookHandler()


@app.post("/webhooks", tags=["ingestion"], status_code=status.HTTP_200_OK)
async def receive_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
) -> dict:
    """Receives a Razorpay webhook: HMAC-verify, freshness-check, dedup, DLQ."""
    body = await request.body()
    headers = (
        {"x-razorpay-signature": x_razorpay_signature} if x_razorpay_signature else {}
    )
    result = _webhook_handler.handle(body, headers)

    if result.status is WebhookResultStatus.ACCEPTED:
        return {"status": "accepted", "event_id": result.event_id}

    if result.status is WebhookResultStatus.DUPLICATE:
        # Replayed/duplicate event id — idempotent, not an error.
        return {"status": "duplicate", "event_id": result.event_id}

    if result.status is WebhookResultStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"event_id": result.event_id, "reason": result.reason},
        )

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"event_id": result.event_id, "reason": result.reason},
    )


# ---------------------------------------------------------------------------
# Router include-points for the other tracks. Wire each one up as its track
# ships its FastAPI routes:
#
#   from app.classifier.hybrid_classifier import router    # TODO include: classifier
#   from app.revenue_risk.exposure_engine import router    # TODO include: revenue_risk
#   from app.optimizer.intervention_optimizer import router  # TODO include: optimizer
#   from app.policy.policy_engine import router            # TODO include: policy
#   from app.executor.action_executor import router        # TODO include: executor
#   from app.reconciliation.reconciliation_engine import router  # TODO include: reconciliation
#   from app.measurement.control_group import router       # TODO include: measurement
#   from app.audit.audit_logger import router              # TODO include: audit
#   from app.health.agent_monitor import router            # TODO include: health
#   from app.modules.payment_degradation import router     # TODO include: modules
#   from app.b2b.customer_profile import router            # TODO include: b2b
#   from app.nlp.ptp_extractor import router               # TODO include: nlp
#   from app.dashboard.api import router                   # TODO include: dashboard
#   from app.dashboard.red_team_api import router          # TODO include: red_team
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Dashboard & red-team routers (Track D) — serve the §14 panels and §16.1 demo.
# ---------------------------------------------------------------------------

app.include_router(dashboard_router)
app.include_router(red_team_router)

_static_dir = Path(__file__).resolve().parent / "dashboard" / "static"
app.mount(
    "/static",
    StaticFiles(directory=str(_static_dir)),
    name="static",
)


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "recoveryos", "version": app.version}
