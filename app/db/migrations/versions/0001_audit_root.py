"""Create core schema + append-only hash-chained audit_log.

Revision ID: 0001_audit_root
Revises:
Create Date: 2026-01-01 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_audit_root"
down_revision = None
branch_labels = None
depends_on = None

_HASH_CHECK = "audit_log_sha256_check"

_PREVENT_MODIFICATION = """
CREATE OR REPLACE FUNCTION audit_log_prevent_modification()
RETURNS TRIGGER AS $function$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only; UPDATE and DELETE are prohibited';
END;
$function$ LANGUAGE plpgsql;
"""

_HASH_CHAIN = """
CREATE OR REPLACE FUNCTION audit_log_hash_chain()
RETURNS TRIGGER AS $function$
DECLARE
    last_hash VARCHAR(64);
    payload TEXT;
BEGIN
    -- serialize inserts so the chain cannot fork under concurrency
    PERFORM pg_advisory_xact_lock(hashtext('audit_log_chain'));

    -- chain linkage: previous_hash must be the last row's current_hash (13.2)
    SELECT current_hash INTO last_hash
    FROM audit_log
    ORDER BY row_seq DESC
    LIMIT 1
    FOR UPDATE;
    NEW.previous_hash := COALESCE(last_hash, repeat('0', 64));

    -- fold the event payload into a fixed-width hash
    NEW.trigger_payload_hash := COALESCE(
        NEW.trigger_payload_hash,
        encode(
            digest(
                COALESCE(NEW.trigger_type, '') || ':' ||
                COALESCE(NEW.trigger_event, '') || ':' ||
                COALESCE(NEW.case_id, '') || ':' ||
                COALESCE(NEW.obligation_id, '') || ':' ||
                COALESCE(NEW.action_type, ''),
                'sha256'
            ),
            'hex'
        )
    );

    NEW.created_at := COALESCE(NEW.created_at, NOW());
    payload := NEW.previous_hash || '|' || NEW.trigger_payload_hash || '|' ||
               to_char(NEW.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
    NEW.current_hash := encode(digest(payload, 'sha256'), 'hex');
    RETURN NEW;
END;
$function$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "recovery_cases",
        sa.Column("case_id", sa.String(length=100), primary_key=True),
        sa.Column("customer_id", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=25), server_default="DETECTED"),
        sa.Column("root_cause", sa.String(length=100), server_default=""),
        sa.Column("fraud_score", sa.Numeric(4, 3), server_default="0"),
        sa.Column("dispute_score", sa.Numeric(4, 3), server_default="0"),
        sa.Column("natural_pay_probability", sa.Numeric(4, 3), server_default="0"),
        sa.Column("best_action", sa.String(length=100), server_default=""),
        sa.Column("uplift_segment", sa.String(length=30), server_default=""),
        sa.Column("total_remaining", sa.BigInteger(), server_default="0"),
        sa.Column("recovery_locked", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("communications", sa.JSON(), nullable=True),
        sa.Column("payment_links", sa.JSON(), nullable=True),
        sa.Column("ptps", sa.JSON(), nullable=True),
        sa.Column("decisions", sa.JSON(), nullable=True),
        sa.Column("audit_events", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_recovery_cases_customer_id", "recovery_cases", ["customer_id"])

    op.create_table(
        "obligations",
        sa.Column("obligation_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=100),
            sa.ForeignKey("recovery_cases.case_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("original_amount", sa.BigInteger(), nullable=False),
        sa.Column("paid_amount", sa.BigInteger(), server_default="0"),
        sa.Column("refunded_amount", sa.BigInteger(), server_default="0"),
        sa.Column("disputed_amount", sa.BigInteger(), server_default="0"),
        sa.Column("currency", sa.String(length=3), server_default="INR"),
        sa.Column("status", sa.String(length=20), server_default="OPEN"),
        sa.Column("customer_id", sa.String(length=100), nullable=True),
        sa.Column("merchant_id", sa.String(length=100), nullable=True),
        sa.Column("razorpay_entity_ids", sa.JSON(), nullable=True),
        sa.Column("recovery_locked", sa.Boolean(), server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_obligations_customer_id", "obligations", ["customer_id"])

    # -- §13.6 audit_log schema ---------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column(
            "row_seq",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("current_hash", sa.String(length=64), nullable=False),
        sa.Column("trigger_type", sa.String(length=50), nullable=False),
        sa.Column("trigger_event", sa.String(length=100), nullable=True),
        sa.Column("trigger_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("razorpay_event_id", sa.String(length=100), nullable=True),
        sa.Column("case_id", sa.String(length=100), nullable=True),
        sa.Column("obligation_id", sa.String(length=100), nullable=True),
        sa.Column("customer_id", sa.String(length=100), nullable=True),
        sa.Column("transaction_id", sa.String(length=100), nullable=True),
        sa.Column("transaction_type", sa.String(length=50), nullable=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="INR"),
        sa.Column("classification", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("classifier_version", sa.String(length=50), nullable=True),
        sa.Column("propensity_model_version", sa.String(length=50), nullable=True),
        sa.Column("uplift_model_version", sa.String(length=50), nullable=True),
        sa.Column("policy_version", sa.String(length=50), nullable=True),
        sa.Column("natural_payment_probability", sa.Numeric(4, 3), nullable=True),
        sa.Column("uplift_segment", sa.String(length=50), nullable=True),
        sa.Column("revenue_at_risk", sa.BigInteger(), nullable=True),
        sa.Column("expected_natural_recovery", sa.BigInteger(), nullable=True),
        sa.Column("expected_incremental_recovery", sa.BigInteger(), nullable=True),
        sa.Column("candidate_actions", sa.JSON(), nullable=True),
        sa.Column("selected_action", sa.String(length=100), nullable=True),
        sa.Column("action_economic_score", sa.Numeric(10, 2), nullable=True),
        sa.Column("rejected_actions", sa.JSON(), nullable=True),
        sa.Column("suppressed_actions", sa.JSON(), nullable=True),
        sa.Column("compliance_checks", sa.JSON(), nullable=True),
        sa.Column("policy_gate_result", sa.String(length=20), nullable=True),
        sa.Column("action_type", sa.String(length=100), nullable=True),
        sa.Column("action_details", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("execution_state", sa.String(length=20), nullable=True),
        sa.Column("outcome", sa.String(length=50), nullable=True),
        sa.Column("outcome_amount_paise", sa.BigInteger(), nullable=True),
        sa.Column("outcome_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_incremental", sa.Boolean(), nullable=True),
        sa.Column("pipeline_run_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_id", sa.String(length=100), nullable=True),
        sa.Column("control_or_treatment", sa.String(length=20), nullable=True),
        sa.Column("attempt_number", sa.Integer(), server_default="1"),
        sa.Column("is_immutable", sa.Boolean(), server_default=sa.text("true")),
        sa.CheckConstraint("current_hash ~ '^[a-f0-9]{64}$'", name=_HASH_CHECK),
    )
    op.create_index("ix_audit_log_row_seq", "audit_log", ["row_seq"], unique=True)
    op.create_index("ix_audit_log_case_id", "audit_log", ["case_id"])

    # -- Append-only enforcement + hash-chain linkage -------------------------
    op.execute(_PREVENT_MODIFICATION)
    op.execute(_HASH_CHAIN)

    op.execute("""
        CREATE TRIGGER audit_log_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW
        EXECUTE FUNCTION audit_log_prevent_modification();
        """)
    op.execute("""
        CREATE TRIGGER audit_log_compute_hash
        BEFORE INSERT ON audit_log
        FOR EACH ROW
        EXECUTE FUNCTION audit_log_hash_chain();
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_compute_hash ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_hash_chain()")
    op.execute("DROP FUNCTION IF EXISTS audit_log_prevent_modification()")
    op.drop_index("ix_audit_log_case_id", table_name="audit_log")
    op.drop_index("ix_audit_log_row_seq", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_obligations_customer_id", table_name="obligations")
    op.drop_table("obligations")
    op.drop_index("ix_recovery_cases_customer_id", table_name="recovery_cases")
    op.drop_table("recovery_cases")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
