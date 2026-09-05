"""Case Inspector API — GET /api/cases/{case_id}/decision-packet (§13.3, §14).

Returns the full structured decision packet for a single case, assembled from
real data already in DB/audit/policy modules (Tracks A-C). Fields without
upstream sources yet (diagnosis rationale, structured policy_gates) return
null with a pending marker (E.3, E.6 done). Channel drafts are generated
per case by the §10.7 template engine with LLM hallucination guardrails (E.6).
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from pathlib import Path
from typing import Any

from app.audit.audit_logger import AuditLogger
from app.audit.decision_trace import DecisionTrace, DecisionTracer
from app.audit.prevention_log import PreventionLog
from app.contracts import Action, ExecutionState, PolicyGateResult
from app.core.obligation import Obligation
from app.core.recovery_case import RecoveryCase
from app.dashboard.render_guard import guard_response
from app.executor.human_queue import HumanTask, HumanTaskQueue, priority_for
from app.executor.notification import NotificationRecord, NotificationSender
from app.executor.voice_agent import (
    DEFAULT_TTS_VOICE,
    VoiceNudge,
    VoiceRecovery,
    VoiceSynthesis,
    VoiceSynthesisError,
)
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

logger = logging.getLogger(__name__)


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
        notification_sender: NotificationSender | None = None,
        voice_agent: VoiceRecovery | None = None,
        voice_synthesis: VoiceSynthesis | None = None,
        voice_save_dir: str | Path | None = None,
        human_queue: HumanTaskQueue | None = None,
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
        self._notifications = notification_sender or NotificationSender()
        self._voice = voice_agent or VoiceRecovery()
        self._synthesis = voice_synthesis or VoiceSynthesis()
        self._voice_nudges: dict[str, VoiceNudge] = {}
        self._voice_save_dir = Path(voice_save_dir) if voice_save_dir is not None else None
        self._human_queue = human_queue or HumanTaskQueue()

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

    # ── Human / AI action execution (E.7) ───────────────────────────

    @dataclasses.dataclass(frozen=True)
    class ActionOutcome:
        """Result of a human-initiated (or AI-attended) action on a case."""

        executed: bool
        action: str
        channel: str | None
        state: str  # SUCCESS | BLOCKED
        detail: str
        policy_blocked_reasons: tuple[str, ...] = ()
        hold_until: str | None = None
        external_ref: str | None = None

    def _reconstruct_case(self, trace: DecisionTrace) -> RecoveryCase:
        """Reconstruct a ReviewCase from the stored decision trace (§13.3).

        The trace is the authoritative case record for the decision-maker; the
        obligation amount comes from revenue_at_risk (paise).
        """
        customer_id = case_customer_id(trace.case_id)
        obligation = Obligation(
            obligation_id=f"OBL_{trace.case_id}",
            type="payment",
            original_amount=trace.revenue_at_risk_paise,
            customer_id=customer_id,
        )
        return RecoveryCase(
            case_id=trace.case_id,
            customer_id=customer_id,
            obligations=[obligation],
            root_cause=trace.root_cause or "",
            fraud_score=0.0,
            natural_pay_probability=trace.natural_payment_probability,
        )

    def action(
        self,
        case_id: str,
        action: Action,
        *,
        channel: str | None = None,
        actor: str,
        reason: str = "",
        at=None,
    ) -> ActionOutcome:
        """Execute a human-initiated action AFTER re-running the policy gates.

        A human override NEVER skips the safety layer (§7.1): every action
        passes through policy_engine.evaluate() first. If any gate fails the
        action is BLOCKED and nothing is executed. The actor (a human's
        identifier, or "AI — unattended") and reason are recorded in a new
        decision trace and an audit entry.
        """
        trace = self._tracer.latest_trace(case_id)
        if trace is None:
            raise KeyError(f"No decision packet for case_id={case_id}")

        case = self._reconstruct_case(trace)

        # ── (a) Re-run the case through the policy engine — a human
        #        override never skips the gates (§7.1, Track E.7). ──
        evaluation = self._policy.evaluate(case, action, at=at)
        if evaluation.result == PolicyGateResult.BLOCKED:
            blocked_reasons = tuple(evaluation.blocked_reasons)
            hold = evaluation.hold_until.isoformat() if evaluation.hold_until else None
            self._record_action(
                case_id=case_id,
                action=action,
                channel=channel,
                actor=actor,
                reason=reason,
                executed=False,
                detail="; ".join(blocked_reasons) or "Blocked by policy gate",
                gate_result="BLOCKED",
                hold_until=hold,
            )
            return self.ActionOutcome(
                executed=False,
                action=action.value,
                channel=channel,
                state="BLOCKED",
                detail="; ".join(blocked_reasons) or "Blocked by policy gate",
                policy_blocked_reasons=blocked_reasons,
                hold_until=hold,
            )

        # ── (b) Dispatch to the matching executor function. ──
        result_ref: str | None = None
        exec_detail = f"{action.value} executed"
        if action == Action.HUMAN_ESCALATION:
            exec_detail = self._enqueue_human_task(case)
        elif action in {Action.SEND_SMS, Action.SEND_EMAIL, Action.SEND_WHATSAPP}:
            result_ref, exec_detail = self._dispatch_send(action, case, trace)
        elif action == Action.VOICE_CALL:
            result_ref, exec_detail = self._dispatch_voice(case)
        elif action == Action.SEND_PAYMENT_LINK:
            result_ref, exec_detail = self._dispatch_send(action, case, trace)
        elif action == Action.OFFER_PARTIAL_PAYMENT:
            exec_detail = "Partial payment offer registered on the case"
        elif action == Action.CREATE_PTP:
            exec_detail = "Promise-to-pay registered on the case"
        elif action == Action.WRITE_OFF:
            exec_detail = self._write_off_case(case)
        elif action in {Action.WAIT, Action.NO_ACTION}:
            exec_detail = f"{action.value}: internal hold — nothing outbound sent"
        elif action == Action.BLOCK:
            exec_detail = "Case hard-blocked"

        self._record_action(
            case_id=case_id,
            action=action,
            channel=channel,
            actor=actor,
            reason=reason,
            executed=True,
            detail=exec_detail,
            gate_result="APPROVED",
            external_ref=result_ref,
        )
        return self.ActionOutcome(
            executed=True,
            action=action.value,
            channel=channel,
            state="SUCCESS",
            detail=exec_detail,
            external_ref=result_ref,
        )

    def _dispatch_send(self, action: Action, case: RecoveryCase, trace: DecisionTrace) -> tuple[str, str]:
        """Send an SMS/Email/WhatsApp/Payment-link via the notification sender."""
        channel_map = {
            Action.SEND_SMS: "sms",
            Action.SEND_EMAIL: "email",
            Action.SEND_WHATSAPP: "whatsapp",
            Action.SEND_PAYMENT_LINK: "sms",
        }
        channel = channel_map[action]
        template = {
            Action.SEND_PAYMENT_LINK: "payment_failed_sms",
        }.get(action, "payment_failed_sms")
        record = self._notifications.send(
            channel,
            template,
            {
                "merchant_name": "Demo Merchant",
                "amount": case.total_remaining() // 100,
                "order_id": trace.case_id,
                "link": f"https://rzp.io/i/pl_{trace.case_id.lower()}",
                "customer_name": f"Customer {case_customer_id(trace.case_id)}",
                "failure_reason": (trace.root_cause or "unknown").replace("_", " "),
                "expiry_date": "2026-09-12",
            },
            recipient=case_customer_id(trace.case_id),
            note=f"Reviewer approval for {trace.case_id}",
        )
        self._outbox.append(record)
        return record.notification_id, f"Notification sent via {channel}"

    def _dispatch_voice(self, case: RecoveryCase) -> tuple[str, str]:
        """Place a (mocked) voice call via the voice agent."""
        from app.executor.voice_agent import MockTelephonyClient

        call_id = MockTelephonyClient().place_call(case.customer_id, {})
        outcome = self._voice.handle_call(call_id, "Customer did not pick up yet")
        return call_id, f"Voice call placed ({outcome.state.value})"

    def get_voice_nudge(self, nudge_id: str) -> VoiceNudge | None:
        """Return a previously synthesized voice nudge by id."""
        return self._voice_nudges.get(nudge_id)

    def generate_voice_nudge(
        self,
        case_id: str,
        register: str,
        *,
        voice: str = DEFAULT_TTS_VOICE,
        actor: str,
    ) -> tuple[VoiceNudge, str]:
        """Synthesize the case's voice_script draft into playable audio (E.8).

        Uses Gemini native TTS via the shared GEMINI_API_KEY. This is a
        SIMULATED outbound call: the audible nudge is saved and a "call"
        record is written to the audit trail — no live phone is dialled.
        Returns (nudge, call_state).
        """
        if register not in REGISTERS:
            raise ValueError(f"Unknown register: {register}")
        packet = self.build_packet(case_id)
        if packet is None:
            raise KeyError(f"No decision packet for case_id={case_id}")
        script = (packet.channel_drafts.voice_script or {}).get(register)
        if not script:
            raise ValueError(f"No voice_script draft for register '{register}'")

        nudge = self._synthesis.synthesize(script, voice=voice)
        self._voice_nudges[nudge.nudge_id] = nudge
        if self._voice_save_dir is not None:
            nudge.save(self._voice_save_dir)
        call_state = self._record_voice_call(
            case_id=case_id,
            register=register,
            nudge=nudge,
            actor=actor,
        )
        logger.info(
            "Voice nudge %s recorded as simulated %s call for %s",
            nudge.nudge_id,
            register,
            case_id,
        )
        return nudge, call_state

    def _record_voice_call(
        self,
        *,
        case_id: str,
        register: str,
        nudge: VoiceNudge,
        actor: str,
    ) -> str:
        """Write the simulated outbound "call" into the append-only audit trail."""
        outcome = self._voice.handle_call(
            call_id=f"call_{nudge.nudge_id}",
            transcript=nudge.text,
        )
        self._audit.append(
            trigger_type="VOICE_CALL_RECORDED",
            trigger_event=f"simulated_outbound_call_{register}",
            payload={
                "actor": actor,
                "register": register,
                "text": nudge.text,
                "audio_id": nudge.nudge_id,
                "state": outcome.state.value,
            },
            case_id=case_id,
            audio_id=nudge.nudge_id,
            voice=nudge.voice,
            model=nudge.model,
            media_type=nudge.media_type,
            duration_seconds=nudge.duration_seconds,
            call_state=outcome.state.value,
            summary=outcome.summary,
            transcript=nudge.text,
            actor=actor,
            register=register,
        )
        return outcome.state.value

    def _enqueue_human_task(self, case: RecoveryCase) -> str:
        """Escalate a case to the human-in-the-loop queue (§8.6)."""
        priority, sla = priority_for(
            case.case_id,
            amount_paise=case.total_remaining(),
            low_confidence=True,
        )
        task = HumanTask(
            task_id=f"escalate_{case.case_id}_{uuid.uuid4().hex[:6]}",
            case_id=case.case_id,
            priority=priority,
            action=Action.HUMAN_ESCALATION,
            proposed_reasoning="Human-initiated escalation from decision panel.",
            sla_minutes=sla,
            amount_paise=case.total_remaining(),
        )
        self._human_queue.enqueue(task)
        return f"Escalated to human queue ({priority.value}, SLA {sla}m)"

    def _write_off_case(self, case: RecoveryCase) -> str:
        """Write off the case's open obligations (terminal ledger state)."""
        written: list[str] = []
        for obligation in case.obligations:
            if not obligation.is_terminal:
                obligation.write_off()
                written.append(obligation.obligation_id)
        return (
            "Case written off (" + (", ".join(written) or "no open obligations") + ")"
        )

    def _record_action(
        self,
        *,
        case_id: str,
        action: Action,
        channel: str | None,
        actor: str,
        reason: str,
        executed: bool,
        detail: str,
        gate_result: str,
        hold_until: str | None = None,
        external_ref: str | None = None,
    ) -> None:
        """Write a decision trace + audit entry for a human/AI action (E.7)."""
        self._audit.append(
            trigger_type="ACTION_EXECUTED" if executed else "ACTION_BLOCKED",
            trigger_event=(
                f"{action.value}_approved_by_{actor}"
                if executed
                else f"{action.value}_blocked_policy"
            ),
            payload={
                "actor": actor,
                "reason": reason or "(no reason given)",
                "executed": executed,
                "channel": channel,
                "detail": detail,
                "policy_gate_result": gate_result,
                "hold_until": hold_until,
            },
            case_id=case_id,
            action=action.value,
            amount_paise=0,
            external_ref=external_ref,
            actor=actor,
            reason=reason or "(no reason given)",
            executed=executed,
            channel=channel,
            detail=detail,
            policy_gate_result=gate_result,
            hold_until=hold_until,
        )


def _pending(step: str) -> dict[str, str]:
    """Create a pending marker dict for fields not yet populated."""
    return {"pending": step}


def case_customer_id(case_id: str) -> str:
    """Stable customer identifier derived from a case id (demo projection)."""
    return f"CUST_{case_id.replace('-', '_').upper()}"


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

from fastapi import APIRouter, HTTPException, Request, Response

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
    notification_sender: NotificationSender | None = None,
    voice_agent: VoiceRecovery | None = None,
    voice_synthesis: VoiceSynthesis | None = None,
    voice_save_dir: str | Path | None = None,
    human_queue: HumanTaskQueue | None = None,
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
        notification_sender=notification_sender,
        voice_agent=voice_agent,
        voice_synthesis=voice_synthesis,
        voice_save_dir=voice_save_dir,
        human_queue=human_queue,
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


def _parse_action(value: str) -> Action:
    """Parse and validate an action string from the decision-panel UI."""
    try:
        return Action(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: {value}",
        ) from exc


@router.post("/api/cases/{case_id}/action")
async def take_action(case_id: str, request: Request) -> dict[str, Any]:
    """Execute a human-initiated (or AI-attended) action on a case.

    The policy engine is ALWAYS re-run first — a human override never skips
    the gates (§7.1). A BLOCKED evaluation returns 409 and nothing executes.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")

    inspector = _get_inspector()
    action = _parse_action(str(payload.get("action") or ""))
    channel = str(payload.get("channel")) if payload.get("channel") else None
    reason = str(payload.get("reason") or "")

    if inspector.build_packet(case_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Decision packet not found for case_id={case_id}",
        )

    actor = str(payload.get("actor") or "AI — unattended")

    try:
        outcome = inspector.action(
            case_id,
            action,
            channel=channel,
            actor=actor,
            reason=reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = {
        "case_id": case_id,
        "executed": outcome.executed,
        "action": outcome.action,
        "channel": outcome.channel,
        "state": outcome.state,
        "detail": outcome.detail,
        "actor": actor,
        "policy_blocked_reasons": list(outcome.policy_blocked_reasons),
        "hold_until": outcome.hold_until,
        "external_ref": outcome.external_ref,
    }
    if not outcome.executed:
        raise HTTPException(status_code=409, detail=data)
    return guard_response(data, f"action:{case_id}").data


@router.post("/api/cases/{case_id}/voice-nudge")
async def generate_voice_nudge(case_id: str, request: Request) -> dict[str, Any]:
    """Synthesize the case's voice_script draft into playable audio (E.8).

    Calls Gemini native TTS (shared GEMINI_API_KEY) and records a simulated
    outbound "call" in the audit trail. Returns an audio_url the browser can
    stream through a standard <audio> player.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")

    register = str(payload.get("register") or "en")
    voice = str(payload.get("voice") or DEFAULT_TTS_VOICE)
    actor = str(payload.get("actor") or "ops.voice_nudge")

    inspector = _get_inspector()
    try:
        nudge, call_state = inspector.generate_voice_nudge(
            case_id, register, voice=voice, actor=actor
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VoiceSynthesisError as exc:
        raise HTTPException(status_code=502, detail=f"Voice synthesis failed: {exc}") from exc

    data = {
        "nudge_id": nudge.nudge_id,
        "case_id": case_id,
        "register": register,
        "text": nudge.text,
        "voice": nudge.voice,
        "model": nudge.model,
        "media_type": nudge.media_type,
        "duration_seconds": nudge.duration_seconds,
        "audio_url": f"/api/cases/{case_id}/voice-nudge/audio/{nudge.nudge_id}",
        "call_state": call_state,
    }
    return guard_response(data, f"voice_nudge:{case_id}").data


@router.get("/api/cases/{case_id}/voice-nudge/audio/{nudge_id}")
def get_voice_nudge_audio(case_id: str, nudge_id: str) -> Response:
    """Stream the synthesized voice nudge WAV for in-browser playback (E.8)."""
    nudge = _get_inspector().get_voice_nudge(nudge_id)
    if nudge is None:
        raise HTTPException(
            status_code=404, detail=f"No voice nudge audio for nudge_id={nudge_id}"
        )
    return Response(
        content=nudge.wav_bytes,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )