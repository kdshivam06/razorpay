"""Dataset-backed demo projection for the dashboard.

This is used only when no live pipeline state has been injected into
DashboardApi. It reads the model-facing synthetic batch plus held-out
evaluation labels to build an honest synthetic demo view.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from pathlib import Path

from app.audit.decision_trace import DecisionTracer, PolicyGateDetail
from app.audit.prevention_log import PreventionCategory, PreventionLog
from app.contracts import Action, CandidateAction
from app.dashboard.api import DashboardApi
from app.executor.human_queue import HumanTask, HumanTaskQueue, priority_for
from app.executor.notification import NotificationSender
from app.policy.legal_basis import get_legal_basis

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BATCH_PATH = PROJECT_ROOT / "data" / "synthetic_batch.csv"
GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "ground_truth.json"

COMMUNICATION_COSTS = {
    Action.SEND_SMS: 25,
    Action.SEND_EMAIL: 10,
    Action.SEND_WHATSAPP: 35,
    Action.SEND_PAYMENT_LINK: 100,
    Action.VOICE_CALL: 2500,
    Action.RETRY_SAME_METHOD: 150,
    Action.RETRY_ALTERNATE_METHOD: 150,
    Action.REQUEST_PAYMENT_METHOD_UPDATE: 100,
    Action.OFFER_PARTIAL_PAYMENT: 100,
    Action.CREATE_PTP: 100,
    Action.HUMAN_ESCALATION: 5000,
}

NO_AUTOMATED_SEGMENTS = {"SURE_THING", "SLEEPING_DOG"}
HUMAN_REVIEW_REASONS = {
    "dispute_filed",
    "risk_block",
    "mandate_revoked_customer",
    "wrong_person",
    "prompt_injection_attempt",
}


def build_synthetic_demo_dashboard(limit: int | None = None) -> DashboardApi:
    """Build a read-only dashboard from the synthetic evaluation dataset."""
    rows = _read_batch()
    if limit is not None:
        rows = rows[:limit]
    return build_dashboard_from_rows(rows, dataset_name="synthetic_batch.csv")


def build_dashboard_from_rows(
    rows: list[dict[str, object]],
    *,
    dataset_name: str,
    truth: dict[str, dict] | None = None,
) -> DashboardApi:
    """Score uploaded or realtime rows into an auditable dashboard projection."""
    truth = truth if truth is not None else _read_ground_truth()
    tracer = DecisionTracer()
    prevention = PreventionLog()
    human_queue = HumanTaskQueue()
    notifications = NotificationSender()
    labels_matched = 0

    with _quiet_demo_bootstrap_logs():
        for row in rows:
            normalized = _normalize_row(row)
            case_id = normalized["case_id"]
            labels = truth.get(case_id)
            if labels:
                labels_matched += 1
            else:
                labels = _estimate_labels(normalized)
            amount = _int(normalized.get("outstanding_amount_paise")) or _int(
                normalized.get("amount_paise")
            )
            natural = _float(labels.get("true_natural_probability"))
            segment = str(
                labels.get("true_uplift_segment")
                or normalized.get("persona")
                or "PERSUADABLE"
            )
            candidates = _candidate_actions(amount, natural, labels)
            best = _best_candidate(candidates)
            selected_action = _selected_action(normalized, segment, best)
            policy_failed = _policy_failures(normalized, segment, selected_action)
            policy_result = "BLOCKED" if policy_failed else "APPROVED"
            treated_probability = min(
                1.0,
                natural
                + (max(0.0, best.economic_score) if best and selected_action else 0.0),
            )
            outcome = (
                "RECOVERED" if _stable_fraction(case_id) <= treated_probability else "PENDING"
            )

            if selected_action is None:
                _log_prevention(prevention, normalized, segment, amount)
            if _needs_human(normalized, amount, policy_failed):
                _enqueue_human_review(human_queue, normalized, amount, policy_failed)
            if selected_action and not policy_failed:
                _send_demo_message(notifications, normalized, selected_action, amount)

            tracer.build(
                case_id=case_id,
                trigger_event=str(normalized.get("event_type") or "synthetic_batch"),
                state="RISK_ASSESSED",
                root_cause=str(normalized.get("root_cause") or normalized.get("failure_reason")),
                natural_payment_probability=natural,
                uplift_segment=segment,
                revenue_at_risk_paise=amount,
                candidate_actions=candidates,
                selected_action=selected_action,
                selected_economic_score=best.economic_score if best else 0.0,
                selection_reasoning=_selection_reason(segment, selected_action),
                timing_rationale="Synthetic demo projection from held-out evaluation labels.",
                rejected_actions=_rejected_actions(candidates, selected_action),
                policy_checks_passed=["consent", "contact_window", "idempotency"]
                if not policy_failed
                else ["idempotency"],
                policy_checks_failed=policy_failed,
                policy_gate_result=policy_result,
                policy_gate_details=_build_policy_gate_details(normalized, segment, selected_action, policy_failed),
                execution_result="SUCCESS" if selected_action and not policy_failed else None,
                execution_detail="Dataset-backed synthetic demo; no external side effect.",
                idempotency_key=f"demo_{case_id}",
                is_incremental=segment == "PERSUADABLE",
                control_group=False,
                outcome=outcome,
                model_versions={
                    "classifier_version": "classifier_v1",
                    "propensity_model_version": "propensity_v1",
                    "uplift_model_version": "uplift_v1",
                    "dataset": "synthetic_batch.csv",
                },
            )

    dataset_summary = {
        "dataset_name": dataset_name,
        "record_count": len(rows),
        "held_out_labels_matched": labels_matched,
        "estimated_records": max(0, len(rows) - labels_matched),
        "audit_traces": sum(len(v) for v in tracer._traces.values()),
        "message_count": notifications.sent_count,
        "human_escalations": len(human_queue._queue),
        "label_policy": (
            "Known synthetic case IDs use held-out ground_truth.json for evaluation; "
            "unknown uploaded rows use model-style fallback estimates."
        ),
    }
    return DashboardApi(
        tracer=tracer,
        prevention=prevention,
        human_queue=human_queue,
        messages=notifications.sent_log,
        dataset_summary=dataset_summary,
    )


class _quiet_demo_bootstrap_logs:
    """Temporarily quiet per-case logs while seeding the in-memory dashboard."""

    _LOGGER_NAMES = (
        "app.audit.decision_trace",
        "app.audit.prevention_log",
        "app.executor.notification",
    )

    def __enter__(self) -> None:
        self._previous_levels = {}
        for name in self._LOGGER_NAMES:
            logger = logging.getLogger(name)
            self._previous_levels[name] = logger.level
            logger.setLevel(logging.WARNING)

    def __exit__(self, *_exc: object) -> None:
        for name, level in self._previous_levels.items():
            logging.getLogger(name).setLevel(level)


def _normalize_row(row: dict[str, object]) -> dict[str, str]:
    case_id = str(row.get("case_id") or f"case_live_{_stable_digest(row)[:10]}")
    amount = _int(row.get("outstanding_amount_paise")) or _int(row.get("amount_paise"))
    if amount <= 0:
        amount = 250_000
    failure_reason = str(row.get("failure_reason") or row.get("root_cause") or "unknown_error")
    return {
        "case_id": case_id,
        "event_id": str(row.get("event_id") or f"evt_live_{_stable_digest(row)[:10]}"),
        "event_type": str(row.get("event_type") or "payment.failed"),
        "customer_id": str(row.get("customer_id") or f"cus_live_{_stable_digest(row)[:8]}"),
        "obligation_id": str(row.get("obligation_id") or f"obl_{case_id}"),
        "amount_paise": str(amount),
        "outstanding_amount_paise": str(amount),
        "payment_method": str(row.get("payment_method") or "upi"),
        "failure_reason": failure_reason,
        "root_cause": str(row.get("root_cause") or _root_cause_for(failure_reason)),
        "persona": str(row.get("persona") or "P2"),
        "channel_preference": str(row.get("channel_preference") or "SMS"),
        "contact_count_7d": str(row.get("contact_count_7d") or "0"),
        "conversation_intent_hint": str(row.get("conversation_intent_hint") or ""),
        "customer_message_sample": str(row.get("customer_message_sample") or ""),
    }


def _estimate_labels(row: dict[str, str]) -> dict[str, object]:
    failure_reason = row["failure_reason"]
    persona = row["persona"]
    natural = {
        "bank_timeout": 0.68,
        "gateway_error": 0.62,
        "already_paid_delayed_webhook": 0.92,
        "checkout_abandoned": 0.22,
        "expired_card": 0.32,
        "insufficient_funds_low": 0.28,
        "insufficient_funds_high": 0.20,
        "subscription_pending": 0.24,
        "overdue_invoice": 0.44,
        "partial_payment": 0.52,
        "mandate_revoked_customer": 0.08,
        "risk_block": 0.04,
        "dispute_filed": 0.06,
        "wrong_person": 0.02,
        "prompt_injection_attempt": 0.08,
    }.get(failure_reason, 0.18)
    if persona in {"P1", "P4"}:
        natural += 0.12
    if persona in {"P3", "P5", "P8"}:
        natural -= 0.10
    natural = _clamp(natural, 0.01, 0.96)

    if failure_reason in {"risk_block", "dispute_filed", "wrong_person", "mandate_revoked_customer"}:
        segment = "SLEEPING_DOG"
    elif natural >= 0.72:
        segment = "SURE_THING"
    elif failure_reason in {"unknown_error", "adversarial_webhook"}:
        segment = "LOST_CAUSE"
    else:
        segment = "PERSUADABLE"

    preferred = row.get("channel_preference", "SMS")
    uplifts = {action.value: 0.0 for action in Action}
    if segment == "PERSUADABLE":
        uplifts.update(
            {
                "SEND_PAYMENT_LINK": 0.24,
                "SEND_SMS": 0.16,
                "SEND_WHATSAPP": 0.20,
                "SEND_EMAIL": 0.08,
                "RETRY_ALTERNATE_METHOD": 0.18,
                "REQUEST_PAYMENT_METHOD_UPDATE": 0.22,
                "OFFER_PARTIAL_PAYMENT": 0.19,
                "CREATE_PTP": 0.14,
            }
        )
    elif segment == "SURE_THING":
        uplifts.update({"SEND_SMS": 0.02, "SEND_EMAIL": 0.01, "SEND_PAYMENT_LINK": 0.02})
    elif segment == "LOST_CAUSE":
        uplifts.update({"SEND_PAYMENT_LINK": 0.04, "SEND_SMS": 0.02, "SEND_EMAIL": 0.01})
    else:
        uplifts.update({"SEND_SMS": -0.08, "SEND_WHATSAPP": -0.10, "VOICE_CALL": -0.12})

    if preferred == "WHATSAPP":
        uplifts["SEND_WHATSAPP"] += 0.05
    elif preferred == "EMAIL":
        uplifts["SEND_EMAIL"] += 0.04
    elif preferred == "SMS":
        uplifts["SEND_SMS"] += 0.04

    return {
        "true_natural_probability": round(natural, 4),
        "true_natural_payment_label": 1 if natural >= 0.5 else 0,
        "true_action_uplift": uplifts,
        "true_best_action": max(uplifts, key=uplifts.get),
        "true_max_uplift": max(uplifts.values()),
        "true_payment_time_days": 2 if natural >= 0.5 else 14,
        "true_uplift_label": 1 if max(uplifts.values()) >= 0.08 else 0,
        "true_uplift_segment": segment,
    }


def _send_demo_message(
    sender: NotificationSender,
    row: dict[str, str],
    action: Action,
    amount: int,
) -> None:
    channel_template = {
        Action.SEND_SMS: ("sms", "payment_failed_sms"),
        Action.SEND_EMAIL: ("email", "payment_failed_email"),
        Action.SEND_WHATSAPP: ("whatsapp", "payment_failed_sms"),
        Action.SEND_PAYMENT_LINK: ("sms", "payment_failed_sms"),
        Action.REQUEST_PAYMENT_METHOD_UPDATE: ("sms", "payment_method_update"),
        Action.OFFER_PARTIAL_PAYMENT: ("whatsapp", "partial_payment_offer"),
        Action.CREATE_PTP: ("sms", "ptp_reminder"),
    }.get(action)
    if channel_template is None:
        return

    channel, template_key = channel_template
    variables = {
        "merchant_name": "Demo Merchant",
        "customer_name": row["customer_id"],
        "amount": f"{amount / 100:,.0f}",
        "total_amount": f"{amount / 100:,.0f}",
        "partial_amount": f"{max(100, amount // 200) / 100:,.0f}",
        "order_id": row["obligation_id"],
        "failure_reason": row["failure_reason"].replace("_", " "),
        "link": f"https://rzp.io/i/demo-{row['case_id'][-6:]}",
        "expiry_date": "2026-09-08",
        "ptp_date": "2026-09-07",
        "cycle": "month",
        "last4": "4242",
    }
    sender.send(
        channel,
        template_key,
        variables,
        recipient="+91XXXXXX1234",
        note="demo transport; no live SMS provider configured",
    )


def _root_cause_for(failure_reason: str) -> str:
    if failure_reason.startswith("insufficient_funds"):
        return "insufficient_funds"
    if failure_reason in {"mandate_revoked_customer", "mandate_revoked_bank", "subscription_pending"}:
        return "mandate_failure"
    if failure_reason == "partial_payment":
        return "overdue_invoice"
    if failure_reason == "dispute_filed":
        return "dispute"
    return failure_reason


def _stable_digest(row: dict[str, object]) -> str:
    payload = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_batch() -> list[dict[str, str]]:
    with BATCH_PATH.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_ground_truth() -> dict[str, dict]:
    with GROUND_TRUTH_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("records", {})


def _candidate_actions(amount: int, natural: float, labels: dict) -> list[CandidateAction]:
    actions = labels.get("true_action_uplift") or {}
    candidates = []
    for raw_action, uplift in actions.items():
        action = _coerce_action(raw_action)
        if action is None:
            continue
        uplift_float = _float(uplift)
        cost = COMMUNICATION_COSTS.get(action, 0)
        candidates.append(
            CandidateAction(
                action=action,
                expected_recovery_paise=round(amount * min(1.0, natural + max(0.0, uplift_float))),
                communication_cost_paise=cost,
                operational_cost_paise=0,
                risk_penalty_paise=0,
                cx_penalty_paise=0,
                economic_score=uplift_float,
            )
        )
    return candidates


def _best_candidate(candidates: list[CandidateAction]) -> CandidateAction | None:
    eligible = [c for c in candidates if c.action not in {Action.NO_ACTION, Action.WAIT, Action.BLOCK}]
    return max(eligible, key=lambda c: c.economic_score, default=None)


def _selected_action(
    row: dict[str, str], segment: str, best: CandidateAction | None
) -> Action | None:
    failure_reason = str(row.get("failure_reason") or "")
    if segment in NO_AUTOMATED_SEGMENTS:
        return None
    if failure_reason in HUMAN_REVIEW_REASONS:
        return Action.BLOCK if failure_reason in {"risk_block", "dispute_filed"} else None
    if best is None or best.economic_score <= 0.04:
        return None
    return best.action


def _policy_failures(
    row: dict[str, str], segment: str, selected_action: Action | None
) -> list[str]:
    failure_reason = str(row.get("failure_reason") or "")
    if failure_reason == "dispute_filed":
        return ["active dispute: halt all outreach"]
    if failure_reason == "risk_block":
        return ["fraud/risk block: no retries"]
    if failure_reason == "mandate_revoked_customer":
        return ["customer revoked mandate: permanent stop"]
    if failure_reason == "wrong_person":
        return ["wrong-person signal: stop disclosure"]
    if failure_reason == "prompt_injection_attempt":
        return ["prompt injection classified; cannot control execution"]
    if segment == "SLEEPING_DOG" and selected_action is not None:
        return ["sleeping dog: intervention may reduce payment probability"]
    return []


def _build_policy_gate_details(
    row: dict[str, str], segment: str, selected_action: Action | None, policy_failed: list[str]
) -> list[PolicyGateDetail]:
    """Build detailed per-gate policy results with legal basis for demo data."""
    failure_reason = str(row.get("failure_reason") or "")
    is_outbound = selected_action in {
        Action.SEND_SMS, Action.SEND_EMAIL, Action.SEND_WHATSAPP,
        Action.VOICE_CALL, Action.SEND_PAYMENT_LINK
    }
    
    details = []
    
    # Gate 1: Consent
    if is_outbound:
        legal_basis, description = get_legal_basis("consent")
        if "consent" not in policy_failed:
            details.append(PolicyGateDetail(
                gate_name="consent", result="PASS",
                reason="TRAI consent granted for channel/payment_recovery",
                legal_basis=legal_basis, legal_basis_description=description
            ))
        else:
            details.append(PolicyGateDetail(
                gate_name="consent", result="FAIL",
                reason="No TRAI consent for channel/payment_recovery",
                legal_basis=legal_basis, legal_basis_description=description
            ))
    else:
        legal_basis, description = get_legal_basis("consent")
        details.append(PolicyGateDetail(
            gate_name="consent", result="SKIPPED",
            reason="Not an outbound communication action",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    # Gate 2: Contact window
    if is_outbound:
        legal_basis, description = get_legal_basis("contact_window")
        if "contact_window" not in policy_failed:
            details.append(PolicyGateDetail(
                gate_name="contact_window", result="PASS",
                reason="Within allowed contact window for channel",
                legal_basis=legal_basis, legal_basis_description=description
            ))
        else:
            details.append(PolicyGateDetail(
                gate_name="contact_window", result="FAIL",
                reason="Outside contact window for channel",
                legal_basis=legal_basis, legal_basis_description=description
            ))
    else:
        legal_basis, description = get_legal_basis("contact_window")
        details.append(PolicyGateDetail(
            gate_name="contact_window", result="SKIPPED",
            reason="Not an outbound communication action",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    # Gate 3: Customer preference
    if is_outbound:
        legal_basis, description = get_legal_basis("customer_preference")
        if "customer_preference" not in policy_failed:
            details.append(PolicyGateDetail(
                gate_name="customer_preference", result="PASS",
                reason="Customer preferences allow contact at this time",
                legal_basis=legal_basis, legal_basis_description=description
            ))
        else:
            details.append(PolicyGateDetail(
                gate_name="customer_preference", result="FAIL",
                reason="Customer preference blocks channel at this time",
                legal_basis=legal_basis, legal_basis_description=description
            ))
    else:
        legal_basis, description = get_legal_basis("customer_preference")
        details.append(PolicyGateDetail(
            gate_name="customer_preference", result="SKIPPED",
            reason="Not an outbound action or preferences engine not configured",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    # Gate 4: Cooldown
    if is_outbound:
        legal_basis, description = get_legal_basis("cooldown")
        if "cooldown" not in policy_failed:
            details.append(PolicyGateDetail(
                gate_name="cooldown", result="PASS",
                reason="No active cooldown for channel",
                legal_basis=legal_basis, legal_basis_description=description
            ))
        else:
            details.append(PolicyGateDetail(
                gate_name="cooldown", result="FAIL",
                reason="Cooldown active for channel",
                legal_basis=legal_basis, legal_basis_description=description
            ))
    else:
        legal_basis, description = get_legal_basis("cooldown")
        details.append(PolicyGateDetail(
            gate_name="cooldown", result="SKIPPED",
            reason="Not an outbound action or cooldown manager not configured",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    # Gate 5: Fraud
    legal_basis, description = get_legal_basis("fraud")
    if "fraud" not in policy_failed and failure_reason != "risk_block":
        details.append(PolicyGateDetail(
            gate_name="fraud", result="PASS",
            reason="Fraud risk LOW allows action",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    else:
        details.append(PolicyGateDetail(
            gate_name="fraud", result="FAIL",
            reason=f"Fraud risk HIGH blocks {selected_action.value if selected_action else 'action'}",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    # Gate 6: Dispute
    legal_basis, description = get_legal_basis("dispute")
    if "dispute" not in policy_failed and failure_reason != "dispute_filed":
        details.append(PolicyGateDetail(
            gate_name="dispute", result="PASS",
            reason="No active dispute on obligations",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    else:
        details.append(PolicyGateDetail(
            gate_name="dispute", result="FAIL",
            reason="Active dispute — all recovery actions halted",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    # Gate 7: Reversibility
    legal_basis, description = get_legal_basis("reversibility")
    amount = _int(row.get("amount_paise")) or _int(row.get("outstanding_amount_paise"))
    requires_human = amount > 10_000_000 and selected_action in {
        Action.RETRY_SAME_METHOD, Action.RETRY_ALTERNATE_METHOD,
        Action.SEND_PAYMENT_LINK, Action.OFFER_PARTIAL_PAYMENT,
        Action.REQUEST_PAYMENT_METHOD_UPDATE
    }
    if requires_human:
        details.append(PolicyGateDetail(
            gate_name="reversibility", result="PASS",
            reason=f"Action {selected_action.value if selected_action else 'NONE'} requires human approval (high value)",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    else:
        details.append(PolicyGateDetail(
            gate_name="reversibility", result="PASS",
            reason=f"Action within autonomy limits",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    # Gate 8: Blast radius
    legal_basis, description = get_legal_basis("blast_radius")
    if is_outbound or selected_action in {
        Action.RETRY_SAME_METHOD, Action.RETRY_ALTERNATE_METHOD,
        Action.SEND_PAYMENT_LINK, Action.OFFER_PARTIAL_PAYMENT,
        Action.REQUEST_PAYMENT_METHOD_UPDATE
    }:
        details.append(PolicyGateDetail(
            gate_name="blast_radius", result="PASS",
            reason="Global rate limits within bounds",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    else:
        details.append(PolicyGateDetail(
            gate_name="blast_radius", result="SKIPPED",
            reason="Action not subject to blast radius limits",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    # Gate 9: Platform awareness
    legal_basis, description = get_legal_basis("platform_awareness")
    if "platform_awareness" not in policy_failed and failure_reason != "already_paid_delayed_webhook":
        details.append(PolicyGateDetail(
            gate_name="platform_awareness", result="PASS",
            reason="No platform duplicate action detected",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    else:
        details.append(PolicyGateDetail(
            gate_name="platform_awareness", result="FAIL",
            reason="Platform already sent notification / action exists",
            legal_basis=legal_basis, legal_basis_description=description
        ))
    
    return details


def _needs_human(row: dict[str, str], amount: int, policy_failed: list[str]) -> bool:
    if policy_failed:
        return True
    return amount > 10_000_000


def _enqueue_human_review(
    queue: HumanTaskQueue,
    row: dict[str, str],
    amount: int,
    policy_failed: list[str],
) -> None:
    priority, sla = priority_for(
        row["case_id"],
        amount_paise=amount,
        fraud_risk_high=str(row.get("failure_reason")) == "risk_block",
        disputed=str(row.get("failure_reason")) == "dispute_filed",
        low_confidence=bool(policy_failed),
    )
    queue.enqueue(
        HumanTask(
            task_id=f"demo_task_{row['case_id']}",
            case_id=row["case_id"],
            priority=priority,
            action=Action.HUMAN_ESCALATION,
            proposed_reasoning="; ".join(policy_failed) or "High-value case requires human approval.",
            sla_minutes=sla,
            amount_paise=amount,
        )
    )


def _log_prevention(
    prevention: PreventionLog, row: dict[str, str], segment: str, amount: int
) -> None:
    failure_reason = str(row.get("failure_reason") or "")
    if failure_reason == "dispute_filed":
        category = PreventionCategory.RECOVERY_AFTER_DISPUTE
        reason = "Recovery after dispute prevented."
    elif failure_reason == "already_paid_delayed_webhook":
        category = PreventionCategory.ALREADY_PAID
        reason = "Already-paid customer contact prevented."
    elif failure_reason == "mandate_revoked_customer":
        category = PreventionCategory.CUSTOMER_PREFERENCE
        reason = "Customer-revoked mandate outreach prevented."
    elif segment == "SLEEPING_DOG":
        category = PreventionCategory.CUSTOMER_PREFERENCE
        reason = "Sleeping-dog intervention prevented."
    else:
        category = PreventionCategory.COOLDOWN_ACTIVE
        reason = "Low incremental uplift contact prevented."
    prevention.log(row["case_id"], "OUTBOUND_RECOVERY", reason, category, amount_saved_paise=amount)


def _selection_reason(segment: str, selected_action: Action | None) -> str:
    if selected_action is None:
        return f"{segment}: no automated action has better expected value."
    return f"{segment}: selected highest positive incremental recovery action."


def _rejected_actions(
    candidates: list[CandidateAction], selected_action: Action | None
) -> dict[str, str]:
    return {
        candidate.action.value: "Lower incremental value or blocked by policy."
        for candidate in candidates
        if selected_action is None or candidate.action != selected_action
    }


def _coerce_action(raw_action: str) -> Action | None:
    try:
        return Action(str(raw_action))
    except ValueError:
        return None


def _stable_fraction(case_id: str) -> float:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
