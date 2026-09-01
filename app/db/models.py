"""SQLAlchemy models mirroring the Core Abstractions plus the §13.6 audit log."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RecoveryCase(Base):
    """Recovery case — mirrors app/core/recovery_case.py."""

    __tablename__ = "recovery_cases"

    case_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(100), index=True)
    state: Mapped[str] = mapped_column(String(25), server_default=text("'DETECTED'"))

    root_cause: Mapped[str] = mapped_column(String(100), server_default=text("''"))
    fraud_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default=text("0"))
    dispute_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default=text("0"))
    natural_pay_probability: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), server_default=text("0")
    )
    best_action: Mapped[str] = mapped_column(String(100), server_default=text("''"))
    uplift_segment: Mapped[str] = mapped_column(String(30), server_default=text("''"))

    total_remaining: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    recovery_locked: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )

    communications: Mapped[list] = mapped_column(JSON, default=list)
    payment_links: Mapped[list] = mapped_column(JSON, default=list)
    ptps: Mapped[list] = mapped_column(JSON, default=list)
    decisions: Mapped[list] = mapped_column(JSON, default=list)
    audit_events: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _updated()

    obligations: Mapped[list[Obligation]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class Obligation(Base):
    """Financial obligation ledger — mirrors app/core/obligation.py."""

    __tablename__ = "obligations"

    obligation_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("recovery_cases.case_id", ondelete="SET NULL")
    )
    type: Mapped[str] = mapped_column(String(50))  # payment/invoice/subscription/mandate
    original_amount: Mapped[int] = mapped_column(BigInteger)
    paid_amount: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    refunded_amount: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    disputed_amount: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    currency: Mapped[str] = mapped_column(String(3), server_default=text("'INR'"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'OPEN'"))
    customer_id: Mapped[str | None] = mapped_column(String(100), index=True)
    merchant_id: Mapped[str | None] = mapped_column(String(100))
    razorpay_entity_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    recovery_locked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _updated()

    case: Mapped[RecoveryCase | None] = relationship(back_populates="obligations")

    @property
    def remaining_amount(self) -> int:
        return self.original_amount - self.paid_amount


class AuditLog(Base):
    """Append-only, hash-chained audit log — schema from §13.6."""

    __tablename__ = "audit_log"

    row_seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), unique=True
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Hash chain (§13.2)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    current_hash: Mapped[str] = mapped_column(String(64))

    trigger_type: Mapped[str] = mapped_column(String(50))
    trigger_event: Mapped[str | None] = mapped_column(String(100))
    trigger_payload_hash: Mapped[str | None] = mapped_column(String(64))
    razorpay_event_id: Mapped[str | None] = mapped_column(String(100))

    case_id: Mapped[str | None] = mapped_column(String(100), index=True)
    obligation_id: Mapped[str | None] = mapped_column(String(100))
    customer_id: Mapped[str | None] = mapped_column(String(100))
    transaction_id: Mapped[str | None] = mapped_column(String(100))
    transaction_type: Mapped[str | None] = mapped_column(String(50))
    amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), server_default=text("'INR'"))

    classification: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    reasoning: Mapped[str | None] = mapped_column(Text)
    classifier_version: Mapped[str | None] = mapped_column(String(50))
    propensity_model_version: Mapped[str | None] = mapped_column(String(50))
    uplift_model_version: Mapped[str | None] = mapped_column(String(50))
    policy_version: Mapped[str | None] = mapped_column(String(50))

    natural_payment_probability: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    uplift_segment: Mapped[str | None] = mapped_column(String(50))
    revenue_at_risk: Mapped[int | None] = mapped_column(BigInteger)
    expected_natural_recovery: Mapped[int | None] = mapped_column(BigInteger)
    expected_incremental_recovery: Mapped[int | None] = mapped_column(BigInteger)

    candidate_actions: Mapped[list | None] = mapped_column(JSON)
    selected_action: Mapped[str | None] = mapped_column(String(100))
    action_economic_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    rejected_actions: Mapped[list | None] = mapped_column(JSON)
    suppressed_actions: Mapped[list | None] = mapped_column(JSON)

    compliance_checks: Mapped[list | None] = mapped_column(JSON)
    policy_gate_result: Mapped[str | None] = mapped_column(String(20))

    action_type: Mapped[str | None] = mapped_column(String(100))
    action_details: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    execution_state: Mapped[str | None] = mapped_column(String(20))

    outcome: Mapped[str | None] = mapped_column(String(50))
    outcome_amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    outcome_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_incremental: Mapped[bool | None] = mapped_column(Boolean)

    pipeline_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    experiment_id: Mapped[str | None] = mapped_column(String(100))
    control_or_treatment: Mapped[str | None] = mapped_column(String(20))
    attempt_number: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    is_immutable: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))