"""Case Inspector API — GET /api/cases/{case_id}/decision-packet (§13.3, §14).

Returns the full structured decision packet for a single case, assembled from
real data already in DB/audit/policy modules (Tracks A-C). Fields without
upstream sources yet (diagnosis rationale, structured policy_gates) return
null with a pending marker (E.3, E.6 done). Channel drafts are generated
per case by the §10.7 template engine with LLM hallucination guardrails (E.6).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTrace, DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.contracts import ExecutionState
from app.dashboard.render_guard import guard_response
from app.executor.notification import NotificationRecord
from app.nlp.message_templates import (
    CHANNELS,
    REGISTERS,
    ChannelDraftGenerator,
    TemplateEngine,
    build_draft_context,
)
from app.optimizer.intervention_optimizer import InterventionOptimizer
from app.policy.policy_engine import PolicyEngine
from app.revenue_risk.exposure_engine import ExposureEngine


@dataclasses.dataclass(frozen=True)
class IngestionData:
    """Raw failure ingestion data (§4.2, Track A)."""
    raw_failure_reason: str | None
    decline_code: str | None
    source: str | None


@dataclasses.dataclass(frozen=True)
class DiagnosisData:
    """Diagnosis / root cause attribution (§5.4, Track B)."""
    fault_attribution: str | None
    taxonomy_code: str | None
    taxonomy_label: str | None
    rationale_text: str | None


@dataclasses.dataclass(frozen=True)
class PolicyGate:
    """One structured policy gate result (§7.1, Track C)."""
    gate_name: str
    result: str  # "PASS" | "FAIL" | "BLOCKED" | "SKIPPED"
    reason: str | None
    legal_basis: str | None
    legal_basis_description: str | None


@dataclasses.dataclass(frozen=True)
class RecommendationData:
    """Selected intervention recommendation (§6.2)."""
    action: str
    requires_human: bool
    confidence: float
    reasoning: str | None


@dataclasses.dataclass(frozen=True)
class SettlementProjection:
    """Financial settlement projection (§4.2)."""
    gross: int  # paise
    mdr_fee: int  # paise
    gst_on_fee: int  # paise
    net_yield: int  # paise
    expected_date: str | None


@dataclasses.dataclass(frozen=True)
class ChannelDrafts:
    """Pre-rendered channel message drafts (§10.7, E.6).

    Each channel maps register → content. Register values are "en"
    (Standard English) or "hi-en" (Hinglish).
    """

    sms: dict[str, str] | None  # register → body
    whatsapp: dict[str, str] | None  # register → body
    email: dict[str, dict[str, str]] | None  # register → {subject, body}
    voice_script: dict[str, str] | None  # register → body


@dataclasses.dataclass(frozen=True)
class DecisionPacket:
    """Complete decision packet for the case inspector."""
    case_id: str
    amount: int  # paise
    status: str
    trace_id: str | None
    ingestion: IngestionData
    diagnosis: DiagnosisData
    policy_gates: list[PolicyGate]
    recommendation: RecommendationData
    settlement_projection: SettlementProjection
    channel_drafts: ChannelDrafts
    language: str  # "en" | "hi-en"


class CaseInspector:
    """Assembles the decision packet from real Track A-C components."""

    def __init__(
        self,
        *,
        tracer: DecisionTracer | None = None,
        audit: AuditLogger | None = None,
        prevention: PreventionLog | None = None,
        exposure_engine: ExposureEngine | None = None,
        optimizer: InterventionOptimizer | None = None,
        policy_engine: PolicyEngine | None = None,
        template_engine: TemplateEngine | None = None,
        draft_generator: ChannelDraftGenerator | None = None,
        outbox: list[object] | None = None,
    ) -> None:
        self._tracer = tracer or DecisionTracer()
        self._audit = audit or AuditLogger()
        self._prevention = prevention or PreventionLog()
        self._exposure = exposure_engine or ExposureEngine()
        self._optimizer = optimizer or InterventionOptimizer()
        self._policy = policy_engine or PolicyEngine()
        self._templates = template_engine or TemplateEngine()
        self._drafts = draft_generator or ChannelDraftGenerator()
        self._outbox = outbox if outbox is not None else []

    def build_packet(self, case_id: str) -> DecisionPacket | None:
        """Build the full decision packet for a case."""
        trace = self._tracer.latest_trace(case_id)
        if not trace:
            return None

        # Get the audit entry for trace_id
        audit_entries = self._audit.entries_for_case(case_id)
        trace_id = audit_entries[-1].entry_id if audit_entries else None

        # Build ingestion data from trace
        ingestion = IngestionData(
            raw_failure_reason=trace.trigger_event,
            decline_code=self._extract_decline_code(trace),
            source=self._extract_source(trace),
        )

        # Build diagnosis data
        diagnosis = DiagnosisData(
            fault_attribution=trace.root_cause or None,
            taxonomy_code=self._map_root_cause_to_taxonomy(trace.root_cause),
            taxonomy_label=self._map_root_cause_to_label(trace.root_cause),
            rationale_text=trace.diagnostic_rationale or None,
        )

        # Build structured policy gates from the trace's policy checks
        # In a real system, we'd re-run policy_engine.evaluate() for the selected action
        # For now, derive from trace's passed/failed lists
        policy_gates = self._build_policy_gates(trace)

        # Build recommendation
        recommendation = RecommendationData(
            action=trace.selected_action.value if trace.selected_action else "NO_ACTION",
            requires_human=trace.requires_human_approval,
            confidence=round(trace.selected_economic_score, 4) if trace.selected_economic_score else 0.0,
            reasoning=trace.reasoning or trace.selection_reasoning or None,
        )

        # Build settlement projection
        settlement = self._build_settlement_projection(trace)

        # Build channel drafts
        channel_drafts = self._build_channel_drafts(trace)

        # Determine language
        language = "en"  # Default; could be enhanced with customer preference

        return DecisionPacket(
            case_id=trace.case_id,
            amount=trace.revenue_at_risk_paise,
            status=trace.state,
            trace_id=trace_id,
            ingestion=ingestion,
            diagnosis=diagnosis,
            policy_gates=policy_gates,
            recommendation=recommendation,
            settlement_projection=settlement,
            channel_drafts=channel_drafts,
            language=language,
        )

    def _extract_decline_code(self, trace: DecisionTrace) -> str | None:
        """Extract decline code from trigger event or metadata."""
        # In real implementation, this would come from the webhook payload
        # For now, infer from root_cause
        decline_map = {
            "insufficient_funds": "INSUFFICIENT_FUNDS",
            "expired_card": "EXPIRED_CARD",
            "bank_timeout": "BANK_TIMEOUT",
            "gateway_error": "GATEWAY_ERROR",
            "checkout_abandoned": "CHECKOUT_ABANDONED",
            "mandate_failure": "MANDATE_FAILURE",
            "overdue_invoice": "OVERDUE_INVOICE",
            "dispute": "DISPUTE",
            "risk_block": "RISK_BLOCK",
        }
        return decline_map.get(trace.root_cause.lower(), "UNKNOWN")

    def _extract_source(self, trace: DecisionTrace) -> str | None:
        """Extract the source system (webhook type, API, etc.)."""
        event = trace.trigger_event.lower()
        if "webhook" in event:
            return "razorpay_webhook"
        elif "api" in event:
            return "razorpay_api"
        elif "batch" in event:
            return "synthetic_batch"
        return "unknown"

    def _map_root_cause_to_taxonomy(self, root_cause: str | None) -> str | None:
        """Map root cause to standard taxonomy code."""
        if not root_cause:
            return None
        taxonomy_map = {
            "insufficient_funds": "RC001",
            "expired_card": "RC002",
            "bank_timeout": "RC003",
            "gateway_error": "RC004",
            "checkout_abandoned": "RC005",
            "mandate_failure": "RC006",
            "ptp_broken": "RC007",
            "overdue_invoice": "RC008",
            "dispute": "RC009",
            "risk_block": "RC010",
        }
        return taxonomy_map.get(root_cause.lower(), "RC999")

    def _map_root_cause_to_label(self, root_cause: str | None) -> str | None:
        """Map root cause to human-readable label."""
        if not root_cause:
            return None
        label_map = {
            "insufficient_funds": "Insufficient Funds",
            "expired_card": "Expired Card",
            "bank_timeout": "Bank Timeout",
            "gateway_error": "Gateway Error",
            "checkout_abandoned": "Checkout Abandoned",
            "mandate_failure": "Mandate Failure",
            "ptp_broken": "Promise-to-Pay Broken",
            "overdue_invoice": "Overdue Invoice",
            "dispute": "Payment Dispute",
            "risk_block": "Risk Block / Fraud",
        }
        return label_map.get(root_cause.lower(), "Unknown")

    def _build_policy_gates(self, trace: DecisionTrace) -> list[PolicyGate]:
        """Build structured policy gates from trace's detailed policy_gate_details.
        
        Uses the per-gate results with legal basis stored in trace.policy_gate_details
        (populated by PolicyEngine.evaluate). Falls back to legacy passed/failed lists
        if details are not available.
        """
        gates = []
        
        # Use detailed gate results if available (Track E.3)
        if trace.policy_gate_details:
            for detail in trace.policy_gate_details:
                gates.append(PolicyGate(
                    gate_name=detail.gate_name,
                    result=detail.result,
                    reason=detail.reason,
                    legal_basis=detail.legal_basis,
                    legal_basis_description=detail.legal_basis_description,
                ))
            return gates
        
        # Fallback: derive from legacy flat lists (backward compatibility)
        # Map trace's policy_checks_passed to PASS gates
        for check in trace.policy_checks_passed:
            gates.append(PolicyGate(
                gate_name=check,
                result="PASS",
                reason=None,
                legal_basis=None,
                legal_basis_description=None,
            ))
        
        # Map trace's policy_checks_failed to FAIL gates
        for check in trace.policy_checks_failed:
            gates.append(PolicyGate(
                gate_name=check,
                result="FAIL",
                reason=f"Policy gate '{check}' blocked the action",
                legal_basis=None,
                legal_basis_description=None,
            ))
        
        # If no gates in trace, provide placeholder
        if not gates:
            gates.append(PolicyGate(
                gate_name="pending_structured_gates",
                result="UNKNOWN",
                reason="Structured policy gate evaluation coming in E.6",
                legal_basis=None,
                legal_basis_description=None,
            ))
        
        return gates

    def _build_settlement_projection(self, trace: DecisionTrace) -> SettlementProjection:
        """Build settlement projection from trace data."""
        gross = trace.revenue_at_risk_paise
        
        # Estimate MDR fee (~2% for cards, ~0.5% for UPI, ~1.5% blended)
        mdr_rate = 0.015
        mdr_fee = round(gross * mdr_rate)
        
        # GST on MDR fee (18%)
        gst_on_fee = round(mdr_fee * 0.18)
        
        net_yield = gross - mdr_fee - gst_on_fee
        
        # Expected date based on natural payment probability
        expected_date = None
        if trace.natural_payment_probability > 0.5:
            expected_date = "2026-09-08"  # Placeholder
        elif trace.natural_payment_probability > 0.2:
            expected_date = "2026-09-12"
        else:
            expected_date = "2026-09-20"
        
        return SettlementProjection(
            gross=gross,
            mdr_fee=mdr_fee,
            gst_on_fee=gst_on_fee,
            net_yield=net_yield,
            expected_date=expected_date,
        )

    def _build_channel_drafts(self, trace: DecisionTrace) -> ChannelDrafts:
        """Build validated SMS/WhatsApp/Email/Voice drafts for the case (§10.7).

        Drafts are generated per register (Standard English / Hinglish) from
        backend-truth variables; any LLM hallucinated amount or date is
        rejected inside the generator (§2.3, §10.7).
        """
        context = build_draft_context(
            case_id=trace.case_id,
            amount_paise=trace.revenue_at_risk_paise,
            trigger_dt=trace.trigger_timestamp,
            root_cause=trace.root_cause,
        )
        drafts = self._drafts.generate(context)
        return ChannelDrafts(
            sms={register: drafts["sms"][register].body for register in REGISTERS},
            whatsapp={
                register: drafts["whatsapp"][register].body for register in REGISTERS
            },
            email={
                register: {
                    "subject": drafts["email"][register].subject,
                    "body": drafts["email"][register].body,
                }
                for register in REGISTERS
            },
            voice_script={
                register: drafts["voice_script"][register].body
                for register in REGISTERS
            },
        )

    def send_draft(
        self, case_id: str, channel: str, register: str, subject: str, body: str
    ) -> NotificationRecord:
        """Mock-send an (edited) draft into the shared message outbox.

        The reviewer may edit the draft before sending; the record carries the
        exact text that was approved.
        """
        if channel not in CHANNELS:
            raise ValueError(f"Unknown channel: {channel}")
        if register not in REGISTERS:
            raise ValueError(f"Unknown register: {register}")
        if not body.strip():
            raise ValueError("Draft body cannot be empty")

        record = NotificationRecord(
            notification_id=f"notif_draft_{case_id[:8]}",
            channel=channel,
            template_key=f"draft:{case_id}:{channel}:{register}",
            rendered_body=body if channel != "email" else f"{subject}\n\n{body}",
            recipient="_reviewer_approved_",
            state=ExecutionState.SUCCESS,
        )
        self._outbox.append(record)
        return record


def _pending(step: str) -> dict[str, str]:
    """Create a pending marker dict for fields not yet populated."""
    return {"pending": step}


def packet_to_dict(packet: DecisionPacket) -> dict[str, Any]:
    """Convert DecisionPacket to JSON-serializable dict with pending markers."""
    return {
        "case_id": packet.case_id,
        "amount": packet.amount,
        "status": packet.status,
        "trace_id": packet.trace_id,
        "ingestion": {
            "raw_failure_reason": packet.ingestion.raw_failure_reason,
            "decline_code": packet.ingestion.decline_code,
            "source": packet.ingestion.source,
        },
        "diagnosis": {
            "fault_attribution": packet.diagnosis.fault_attribution,
            "taxonomy_code": packet.diagnosis.taxonomy_code,
            "taxonomy_label": packet.diagnosis.taxonomy_label,
            "rationale_text": _pending("E.3 — diagnosis rationale engine"),
        },
        "policy_gates": [
            {
                "gate_name": gate.gate_name,
                "result": gate.result,
                "reason": gate.reason,
                "legal_basis": gate.legal_basis,
                "legal_basis_description": gate.legal_basis_description,
            }
            for gate in packet.policy_gates
        ] or [_pending("E.6 — structured policy gate evaluation")],
        "recommendation": {
            "action": packet.recommendation.action,
            "requires_human": packet.recommendation.requires_human,
            "confidence": packet.recommendation.confidence,
            "reasoning": packet.recommendation.reasoning,
        },
        "settlement_projection": {
            "gross": packet.settlement_projection.gross,
            "mdr_fee": packet.settlement_projection.mdr_fee,
            "gst_on_fee": packet.settlement_projection.gst_on_fee,
            "net_yield": packet.settlement_projection.net_yield,
            "expected_date": packet.settlement_projection.expected_date,
        },
        "channel_drafts": {
            "sms": packet.channel_drafts.sms or _pending("E.6 — draft engine not configured"),
            "whatsapp": packet.channel_drafts.whatsapp or _pending("E.6 — draft engine not configured"),
            "email": packet.channel_drafts.email or _pending("E.6 — draft engine not configured"),
            "voice_script": packet.channel_drafts.voice_script or _pending("E.6 — draft engine not configured"),
        },
        "language": packet.language,
    }


# ── FastAPI router ────────────────────────────────────────────────────────

import threading

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["case-inspector"])

_inspector: CaseInspector | None = None
_inspector_lock = threading.Lock()


def configure(
    *,
    tracer: DecisionTracer | None = None,
    audit: AuditLogger | None = None,
    prevention: PreventionLog | None = None,
    exposure_engine: ExposureEngine | None = None,
    optimizer: InterventionOptimizer | None = None,
    policy_engine: PolicyEngine | None = None,
    template_engine: TemplateEngine | None = None,
    draft_generator: ChannelDraftGenerator | None = None,
    outbox: list[object] | None = None,
) -> None:
    """Bind the case inspector to the SAME populated components the recovery run used."""
    global _inspector
    _inspector = CaseInspector(
        tracer=tracer,
        audit=audit,
        prevention=prevention,
        exposure_engine=exposure_engine,
        optimizer=optimizer,
        policy_engine=policy_engine,
        template_engine=template_engine,
        draft_generator=draft_generator,
        outbox=outbox,
    )


def _get_inspector() -> CaseInspector:
    global _inspector
    if _inspector is None:
        with _inspector_lock:
            if _inspector is not None:
                return _inspector
            # Fallback to empty instances — should be overridden by demo bootstrap
            _inspector = CaseInspector()
    return _inspector


@router.get("/api/cases/{case_id}/decision-packet")
def get_decision_packet(case_id: str) -> dict[str, Any]:
    """Return the full structured decision packet for a case (§13.3, §14)."""
    inspector = _get_inspector()
    packet = inspector.build_packet(case_id)
    
    if packet is None:
        raise HTTPException(
            status_code=404,
            detail=f"Decision packet not found for case_id={case_id}",
        )
    
    data = packet_to_dict(packet)
    return guard_response(data, f"decision_packet:{case_id}").data


@router.post("/api/cases/{case_id}/drafts/send")
async def send_draft(case_id: str, request: Request) -> dict[str, Any]:
    """Mock-send a reviewer-approved draft into the shared message outbox (E.6)."""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")

    if _get_inspector().build_packet(case_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Decision packet not found for case_id={case_id}",
        )
    try:
        record = _get_inspector().send_draft(
            case_id=case_id,
            channel=str(payload.get("channel", "")),
            register=str(payload.get("register", "en")),
            subject=str(payload.get("subject", "")),
            body=str(payload.get("body", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = {
        "status": "queued",
        "notification_id": record.notification_id,
        "channel": record.channel,
        "case_id": case_id,
    }
    return guard_response(data, f"draft_send:{case_id}").data