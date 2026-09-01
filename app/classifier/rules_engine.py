"""Deterministic root-cause rules — level-1 of the §2.4 fallback hierarchy."""

from __future__ import annotations

import dataclasses
from typing import Any

from app.contracts import RootCause
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class RuleVerdict:
    """A single deterministic rule outcome."""

    name: str
    applies: bool
    root_cause: RootCause | None
    reason: str
    priority: int = 0


class RulesEngine:
    """Fast, explainable, rule-based classification covering the mandatory
    differentiators the plan forbids hiding behind ML:

      - terminal vs transient failures (§9.3)
      - mandate revoked by CUSTOMER (permanent stop) vs by BANK (re-auth ok)
      - dispute / fraud immediate-halt locks
    """

    def evaluate(self, case: RecoveryCase, event: dict) -> list[RuleVerdict]:
        event_text = _event_text(event)
        failure_reason = _event_value(event, "failure_reason", "error_code", "code")
        root_cause = _classify_event(event)
        direction = mandate_revocation_direction(
            str(event.get("error_source", event.get("source", ""))),
            str(event.get("error_description", event.get("description", ""))),
        )
        verdicts = [dispute_or_fraud_halt(case)]

        if direction == "CUSTOMER":
            verdicts.append(
                RuleVerdict(
                    name="mandate_revoked_by_customer",
                    applies=True,
                    root_cause=RootCause.MANDATE_FAILURE,
                    reason="Customer-revoked mandate is a permanent compliance stop.",
                    priority=95,
                )
            )
        elif direction == "BANK":
            verdicts.append(
                RuleVerdict(
                    name="mandate_revoked_by_bank",
                    applies=True,
                    root_cause=RootCause.MANDATE_FAILURE,
                    reason="Bank-revoked mandate can be recovered with re-auth.",
                    priority=85,
                )
            )

        if root_cause is not None:
            verdicts.append(
                RuleVerdict(
                    name=f"failure_reason:{failure_reason or 'event'}",
                    applies=True,
                    root_cause=root_cause,
                    reason=f"Deterministic mapping from event text: {event_text[:160]}",
                    priority=_priority_for(root_cause, failure_reason),
                )
            )

        terminal = is_terminal_failure(
            str(event.get("error_source", event.get("source", ""))),
            str(
                event.get("error_description", event.get("description", failure_reason))
            ),
        )
        if terminal and root_cause is not None:
            verdicts.append(
                RuleVerdict(
                    name="terminal_failure",
                    applies=True,
                    root_cause=root_cause,
                    reason="Terminal failure requires non-retry handling.",
                    priority=80,
                )
            )

        if not any(verdict.applies for verdict in verdicts):
            verdicts.append(
                RuleVerdict(
                    name="no_rule_match",
                    applies=False,
                    root_cause=None,
                    reason="No deterministic root-cause rule matched.",
                    priority=0,
                )
            )
        return sorted(verdicts, key=lambda verdict: verdict.priority, reverse=True)

    def classify_with_rules(self, case: RecoveryCase, event: dict) -> RuleVerdict:
        for verdict in self.evaluate(case, event):
            if verdict.applies and verdict.root_cause is not None:
                return verdict
        return RuleVerdict(
            name="rules_unknown",
            applies=False,
            root_cause=None,
            reason="Rules did not produce a decisive root cause.",
            priority=0,
        )


def is_terminal_failure(error_source: str, error_description: str) -> bool:
    """Terminal failures end recovery; transient failures allow retry (§9.1, §9.3)."""
    text = _normalize(f"{error_source} {error_description}")
    transient_tokens = (
        "bank_timeout",
        "gateway_error",
        "timeout",
        "temporar",
        "try_again",
        "issuer_unavailable",
        "network",
        "downtime",
    )
    terminal_tokens = (
        "expired_card",
        "card_expired",
        "mandate_revoked_customer",
        "revoked_by_customer",
        "customer_revoked",
        "wrong_person",
        "dispute",
        "fraud",
        "risk_block",
        "opt_out",
        "do_not_debit",
        "cancelled_mandate",
        "canceled_mandate",
    )
    if any(token in text for token in transient_tokens):
        return False
    return any(token in text for token in terminal_tokens)


def mandate_revocation_direction(error_source: str, error_description: str) -> str:
    """Return "CUSTOMER" | "BANK" | "UNKNOWN". The CUSTOMER direction is the
    #1 compliance trap — a permanent stop, never retried (§9.3)."""
    text = _normalize(f"{error_source} {error_description}")
    if "mandate" not in text and "revok" not in text and "nach" not in text:
        return "UNKNOWN"
    customer_tokens = (
        "customer",
        "user",
        "payer",
        "revoked_by_customer",
        "customer_revoked",
        "cancelled",
        "canceled",
        "withdrawn_consent",
        "revoked_consent",
        "do_not_debit",
    )
    bank_tokens = (
        "bank",
        "issuer",
        "sponsor",
        "npc",
        "nach",
        "revoked_by_bank",
        "bank_revoked",
        "account_closed",
        "mandate_inactive_at_bank",
    )
    if any(token in text for token in customer_tokens):
        return "CUSTOMER"
    if any(token in text for token in bank_tokens):
        return "BANK"
    return "UNKNOWN"


def dispute_or_fraud_halt(case: RecoveryCase) -> RuleVerdict:
    """Immediate-halt rule for dispute/fraud (§7.1, §15.5 fraud/dispute rows)."""
    root = _normalize(str(case.root_cause))
    if case.dispute_score >= 0.50 or "dispute" in root:
        return RuleVerdict(
            name="dispute_halt",
            applies=True,
            root_cause=RootCause.DISPUTE,
            reason="Case is disputed; recovery must halt pending review.",
            priority=100,
        )
    if case.fraud_score >= 0.50 or "risk_block" in root or "fraud" in root:
        return RuleVerdict(
            name="fraud_halt",
            applies=True,
            root_cause=RootCause.RISK_BLOCK,
            reason="Fraud/risk signal requires fail-closed handling.",
            priority=100,
        )
    return RuleVerdict(
        name="dispute_or_fraud_halt",
        applies=False,
        root_cause=None,
        reason="No dispute or fraud halt signal.",
        priority=0,
    )


FAILURE_REASON_TO_ROOT_CAUSE: dict[str, RootCause] = {
    "insufficient_funds": RootCause.INSUFFICIENT_FUNDS,
    "insufficient_funds_low": RootCause.INSUFFICIENT_FUNDS,
    "insufficient_funds_high": RootCause.INSUFFICIENT_FUNDS,
    "low_balance": RootCause.INSUFFICIENT_FUNDS,
    "expired_card": RootCause.EXPIRED_CARD,
    "card_expired": RootCause.EXPIRED_CARD,
    "bank_timeout": RootCause.BANK_TIMEOUT,
    "issuer_timeout": RootCause.BANK_TIMEOUT,
    "already_paid_delayed_webhook": RootCause.BANK_TIMEOUT,
    "gateway_error": RootCause.GATEWAY_ERROR,
    "gateway_down": RootCause.GATEWAY_ERROR,
    "checkout_abandoned": RootCause.CHECKOUT_ABANDONED,
    "subscription_pending": RootCause.MANDATE_FAILURE,
    "mandate_failure": RootCause.MANDATE_FAILURE,
    "mandate_revoked": RootCause.MANDATE_FAILURE,
    "mandate_revoked_customer": RootCause.MANDATE_FAILURE,
    "mandate_revoked_bank": RootCause.MANDATE_FAILURE,
    "ptp_broken": RootCause.PTP_BROKEN,
    "promise_to_pay_broken": RootCause.PTP_BROKEN,
    "overdue_invoice": RootCause.OVERDUE_INVOICE,
    "partial_payment": RootCause.OVERDUE_INVOICE,
    "risk_block": RootCause.RISK_BLOCK,
    "fraud": RootCause.RISK_BLOCK,
    "fraud_block": RootCause.RISK_BLOCK,
    "intl_decline": RootCause.INTRA_DECLINE,
    "international_decline": RootCause.INTRA_DECLINE,
    "dispute": RootCause.DISPUTE,
    "dispute_filed": RootCause.DISPUTE,
    "customer_dispute": RootCause.DISPUTE,
    "unknown_error": RootCause.UNKNOWN_ERROR,
    "malformed_error": RootCause.UNKNOWN_ERROR,
    "wrong_person": RootCause.UNKNOWN_ERROR,
    "adversarial_webhook": RootCause.UNKNOWN_ERROR,
    "prompt_injection_attempt": RootCause.UNKNOWN_ERROR,
}


EVENT_TYPE_TO_ROOT_CAUSE: dict[str, RootCause] = {
    "checkout.abandoned": RootCause.CHECKOUT_ABANDONED,
    "invoice.overdue": RootCause.OVERDUE_INVOICE,
    "invoice.partially_paid": RootCause.OVERDUE_INVOICE,
    "mandate.revoked": RootCause.MANDATE_FAILURE,
    "subscription.pending": RootCause.MANDATE_FAILURE,
    "dispute.created": RootCause.DISPUTE,
}


def _classify_event(event: dict[str, Any]) -> RootCause | None:
    for key in (
        "failure_reason",
        "root_cause",
        "error_code",
        "code",
        "reason",
        "category",
    ):
        value = _event_value(event, key)
        if value in FAILURE_REASON_TO_ROOT_CAUSE:
            return FAILURE_REASON_TO_ROOT_CAUSE[value]
    event_type = _event_value(event, "event_type", "type")
    if event_type in EVENT_TYPE_TO_ROOT_CAUSE:
        return EVENT_TYPE_TO_ROOT_CAUSE[event_type]
    text = _event_text(event)
    for token, root_cause in FAILURE_REASON_TO_ROOT_CAUSE.items():
        if token in text:
            return root_cause
    return None


def _priority_for(root_cause: RootCause, failure_reason: str) -> int:
    if root_cause in {RootCause.DISPUTE, RootCause.RISK_BLOCK}:
        return 96
    if failure_reason in {"mandate_revoked_customer", "wrong_person"}:
        return 94
    if root_cause in {RootCause.MANDATE_FAILURE, RootCause.UNKNOWN_ERROR}:
        return 75
    return 70


def _event_value(event: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in event and event[key] is not None:
            return _normalize(str(event[key]))
    return ""


def _event_text(event: dict[str, Any]) -> str:
    return _normalize(" ".join(str(value) for value in event.values()))


def _normalize(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")
