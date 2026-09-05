"""Canonical cross-track contract vocabulary for RecoveryOS (freeze point).

This module is the single source of truth for the enums and records that
multiple tracks share (action set, root-cause taxonomy, monetary record
shapes). Tracks MUST reuse these names instead of inventing parallel ones.

Specified in the plan:
  - Action set            §6.1   (the 14-action expanded set)
  - Action impact level   §6.4   (Low / Medium / High autonomy)
  - Root-cause taxonomy   §15.5 / strategies/*.yaml
  - Uplift segments       §5.4   (defined in app/core/recovery_case.py)
  - Contact channels      §5.6
  - Stop reasons          §6.9   (Financial / Customer / Compliance-Risk)
  - Conversation intents  §10.1  (14-intent taxonomy)
  - Consent status        §7.3
  - Execution state       §8.3 / §13.6 (SUCCESS / FAILED / UNKNOWN)
  - Payment-link states   §8.4
  - Subscription states   §9.3
  - Human-queue priority  §8.6   (P0–P4 with SLAs)
  - RiskAssessment record §4.2   (per-case calculation JSON)
  - CandidateAction       §6.2   (economic record per candidate action)

All monetary amounts are integer paise unless a field is explicitly ratio,
probability, score, or a JSON amount in a free-form dict.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Action(str, enum.Enum):
    """The expanded action set from §6.1. NO_ACTION is first-class."""

    NO_ACTION = "NO_ACTION"
    WAIT = "WAIT"
    RETRY_SAME_METHOD = "RETRY_SAME_METHOD"
    RETRY_ALTERNATE_METHOD = "RETRY_ALTERNATE_METHOD"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    SEND_SMS = "SEND_SMS"
    SEND_EMAIL = "SEND_EMAIL"
    SEND_WHATSAPP = "SEND_WHATSAPP"
    VOICE_CALL = "VOICE_CALL"
    REQUEST_PAYMENT_METHOD_UPDATE = "REQUEST_PAYMENT_METHOD_UPDATE"
    OFFER_PARTIAL_PAYMENT = "OFFER_PARTIAL_PAYMENT"
    CREATE_PTP = "CREATE_PTP"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"
    WRITE_OFF = "WRITE_OFF"
    BLOCK = "BLOCK"


class ActionImpactLevel(str, enum.Enum):
    """Actor autonomy classification from §6.4."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ContactChannel(str, enum.Enum):
    """Per-customer channels tracked by Channel Affinity (§5.6)."""

    SMS = "SMS"
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    VOICE_CALL = "VOICE_CALL"
    PAYMENT_LINK = "PAYMENT_LINK"


class RootCause(str, enum.Enum):
    """Root-cause taxonomy aligning §15.5 and strategies/*.yaml."""

    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_CARD = "expired_card"
    BANK_TIMEOUT = "bank_timeout"
    GATEWAY_ERROR = "gateway_error"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    MANDATE_FAILURE = "mandate_failure"
    PTP_BROKEN = "ptp_broken"
    OVERDUE_INVOICE = "overdue_invoice"
    DISPUTE = "dispute"
    RISK_BLOCK = "risk_block"
    INTRA_DECLINE = "intl_decline"
    UNKNOWN_ERROR = "unknown_error"


class ConversationalIntent(str, enum.Enum):
    """The 14-intent taxonomy from §10.1."""

    PROMISE_TO_PAY = "PROMISE_TO_PAY"
    PAYMENT_DISPUTE = "PAYMENT_DISPUTE"
    AMOUNT_DISPUTE = "AMOUNT_DISPUTE"
    ALREADY_PAID = "ALREADY_PAID"
    DUPLICATE_CHARGE = "DUPLICATE_CHARGE"
    UNABLE_TO_PAY = "UNABLE_TO_PAY"
    REQUEST_PAYMENT_LINK = "REQUEST_PAYMENT_LINK"
    REQUEST_INVOICE = "REQUEST_INVOICE"
    REQUEST_BANK_DETAILS = "REQUEST_BANK_DETAILS"
    OPT_OUT = "OPT_OUT"
    WRONG_PERSON = "WRONG_PERSON"
    FRAUD_CLAIM = "FRAUD_CLAIM"
    SERVICE_NOT_RECEIVED = "SERVICE_NOT_RECEIVED"
    CANCEL_REQUEST = "CANCEL_REQUEST"


class Emotion(str, enum.Enum):
    """Customer emotion labels from §10.4 (conversation side)."""

    NEUTRAL = "NEUTRAL"
    ANGRY = "ANGRY"
    FRUSTRATED = "FRUSTRATED"
    ANXIOUS = "ANXIOUS"
    SAD = "SAD"
    POSITIVE = "POSITIVE"


class HumanPriority(str, enum.Enum):
    """Human-in-the-loop queue priorities from §8.6."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class RiskLevel(str, enum.Enum):
    """Decision / fraud risk level shared by fraud detector and policy gate."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ConsentStatus(str, enum.Enum):
    """TRAI communication consent status from §7.3."""

    GRANTED = "GRANTED"
    REVOKED = "REVOKED"
    UNKNOWN = "UNKNOWN"


class ExecutionState(str, enum.Enum):
    """External API result state from §8.3 / §13.6."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class PolicyGateResult(str, enum.Enum):
    """Master policy gate verdict from §13.6."""

    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"


class ProcessingStatus(str, enum.Enum):
    """Event-inbox processing status from §11.1."""

    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"


class PaymentLinkState(str, enum.Enum):
    """Payment-link lifecycle states from §8.4."""

    CREATED = "CREATED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class SubscriptionState(str, enum.Enum):
    """Subscription states from §9.3."""

    CREATED = "created"
    AUTHENTICATED = "authenticated"
    ACTIVE = "active"
    PENDING = "pending"
    HALTED = "halted"
    CANCELLED = "cancelled"


class RecoveryStopReason(str, enum.Enum):
    """The three stopping-rule classes from §6.9."""

    FINANCIAL = "FINANCIAL"
    CUSTOMER = "CUSTOMER"
    COMPLIANCE_RISK = "COMPLIANCE_RISK"


@dataclass(frozen=True)
class RiskAssessment:
    """Per-case revenue calculation from §4.2. Amounts are integer paise."""

    amount_at_risk: int
    amount_paid: int
    amount_refunded: int
    amount_disputed: int
    amount_remaining: int
    natural_payment_probability: float
    expected_natural_recovery: int
    expected_days_to_payment: float
    fraud_probability: float
    dispute_probability: float
    incremental_recovery_opportunity: int


@dataclass(frozen=True)
class CandidateAction:
    """One candidate action and its economics from §6.2."""

    action: Action
    expected_recovery_paise: int
    communication_cost_paise: int
    operational_cost_paise: int
    risk_penalty_paise: int
    cx_penalty_paise: int
    economic_score: float


@dataclass(frozen=True)
class ConversationResult:
    """Structured NLP extraction result shared across the conversation track
    (§10). Reused by the voice agent and the PTP tracker."""

    intent: ConversationalIntent
    emotion: Emotion
    opt_out_intent: bool
    ptp: dict | None = None
    confidence: float = 1.0
    raw_text: str = ""
