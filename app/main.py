"""FastAPI entry point — wiring config, DB session, ingestion, all track routers,
dashboard, static assets, and a /health endpoint."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.session import get_db  # noqa: F401  (exposes the session dependency)
from app.ingestion.webhook_handler import WebhookHandler, WebhookResultStatus

settings = get_settings()

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger("recoveryos")

app = FastAPI(
    title="RecoveryOS — AI Revenue Recovery Optimizer",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_startup_ts = time.time()

# ---------------------------------------------------------------------------
# 1. Ingestion (Event Gateway) — inline, no separate router
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
# 2. Classifier — Root Cause Engine (§3)
#    from app.classifier.hybrid_classifier import router
#    app.include_router(classifier_router, prefix="/api/classifier")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 3. Revenue Risk — Exposure Engine (§4)
#    from app.revenue_risk.exposure_engine import router
#    app.include_router(revenue_risk_router, prefix="/api/revenue-risk")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 4. Optimizer — Intervention Optimizer (§5)
#    from app.optimizer.intervention_optimizer import router
#    app.include_router(optimizer_router, prefix="/api/optimizer")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 5. Policy — Master Policy Gate (§7)
#    from app.policy.policy_engine import router
#    app.include_router(policy_router, prefix="/api/policy")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 6. Executor — Action Executor (§8)
#    from app.executor.action_executor import router
#    app.include_router(executor_router, prefix="/api/executor")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 7. Reconciliation — Unknown State Handler + Pre-Action (§9)
#    from app.reconciliation.reconciliation_engine import router
#    app.include_router(reconciliation_router, prefix="/api/reconciliation")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 8. Measurement — Control Group + Experiment Engine (§10)
#    from app.measurement.control_group import router
#    app.include_router(measurement_router, prefix="/api/measurement")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 9. Audit — Audit Logger + Prevention Log (§11, §13)
#    from app.audit.audit_logger import router
#    app.include_router(audit_router, prefix="/api/audit")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 10. Health / Agent Monitor (§12, §14.6)
#     from app.health.agent_monitor import router
#     app.include_router(agent_monitor_router, prefix="/api/agent-health")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 11. Modules — Payment Degradation (§6.5)
#     from app.modules.payment_degradation import router
#     app.include_router(modules_router, prefix="/api/modules")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 12. B2B — Customer Profile (§6.8)
#     from app.b2b.customer_profile import router
#     app.include_router(b2b_router, prefix="/api/b2b")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 13. NLP — PTP Extractor + Sentiment (§6.6, §6.7)
#     from app.nlp.ptp_extractor import router
#     app.include_router(nlp_router, prefix="/api/nlp")
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 14. Dashboard — Recovery Waterfall + Scorecard (§14)
# ---------------------------------------------------------------------------

from app.dashboard.api import router as dashboard_router
from app.dashboard.case_inspector import router as case_inspector_router

app.include_router(dashboard_router)
app.include_router(case_inspector_router)

# ---------------------------------------------------------------------------
# 15. Red Team — Attack the Agent demo (§16.1, §21.3)
# ---------------------------------------------------------------------------

from app.dashboard.red_team_api import router as red_team_router

app.include_router(red_team_router)

# ---------------------------------------------------------------------------
# Static assets — §17.1 plain HTML + Chart.js
# ---------------------------------------------------------------------------

_static_dir = Path(__file__).resolve().parent / "dashboard" / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# Auto-load synthetic demo data on startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _auto_load_synthetic_demo():
    """Pre-populate the dashboard with the 2,184-row synthetic batch so the
    dashboard is never empty when opened."""
    try:
        from app.dashboard.demo_data import build_synthetic_demo_dashboard
        from app.dashboard.api import configure as configure_dashboard

        api = build_synthetic_demo_dashboard()
        configure_dashboard(api)
        logger.info("Auto-loaded synthetic demo data (%s traces)", len(api._traces()))
    except Exception:  # noqa: BLE001
        logger.warning("Could not auto-load synthetic demo data", exc_info=True)


# ---------------------------------------------------------------------------
# Meta endpoints — /health, /
# ---------------------------------------------------------------------------


def _check_db() -> tuple[bool, str]:
    """Lightweight DB ping: SELECT 1.  Returns (ok, reason)."""
    try:
        from sqlalchemy import text
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return True, "postgres reachable"
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"postgres unreachable: {exc}"


def _check_redis() -> tuple[bool, str]:
    """Lightweight Redis PING.  Returns (ok, reason)."""
    try:
        import redis as _redis  # type: ignore[import-untyped]

        client = _redis.from_url(settings.REDIS_URL, socket_timeout=3)
        client.ping()
        client.close()
        return True, "redis reachable"
    except ImportError:
        return False, "redis package not installed"
    except Exception as exc:  # noqa: BLE001
        return False, f"redis unreachable: {exc}"


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Lightweight liveness + dependency checks.

    Returns HTTP 200 when the process is alive (always), with per-dependency
    status in the body.  Callers that need strict health gating can inspect
    ``{"status": "degraded"}`` and fail-over accordingly.
    """
    db_ok, db_msg = _check_db()
    redis_ok, redis_msg = _check_redis()

    status_label = "healthy" if (db_ok and redis_ok) else "degraded"

    return {
        "status": status_label,
        "version": app.version,
        "uptime_seconds": round(time.time() - _startup_ts, 1),
        "checks": {
            "postgres": {"ok": db_ok, "detail": db_msg},
            "redis": {"ok": redis_ok, "detail": redis_msg},
        },
    }


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "recoveryos", "version": app.version}
