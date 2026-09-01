"""Tests for the root-cause classifier."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.classifier.hybrid_classifier import HybridClassifier
from app.classifier.ml_classifier import ClassificationModel
from app.classifier.rules_engine import (
    RulesEngine,
    is_terminal_failure,
    mandate_revocation_direction,
)
from app.contracts import RootCause
from app.core.recovery_case import RecoveryCase
from app.revenue_risk.risk_features import RiskFeatures

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BATCH_PATH = PROJECT_ROOT / "data" / "synthetic_batch.csv"
MODEL_PATH = PROJECT_ROOT / "app" / "classifier" / "models" / "classifier_v1.json"


FAILURE_CASES = (
    ("insufficient_funds_low", RootCause.INSUFFICIENT_FUNDS),
    ("insufficient_funds_high", RootCause.INSUFFICIENT_FUNDS),
    ("expired_card", RootCause.EXPIRED_CARD),
    ("bank_timeout", RootCause.BANK_TIMEOUT),
    ("gateway_error", RootCause.GATEWAY_ERROR),
    ("checkout_abandoned", RootCause.CHECKOUT_ABANDONED),
    ("subscription_pending", RootCause.MANDATE_FAILURE),
    ("mandate_revoked_customer", RootCause.MANDATE_FAILURE),
    ("mandate_revoked_bank", RootCause.MANDATE_FAILURE),
    ("overdue_invoice", RootCause.OVERDUE_INVOICE),
    ("partial_payment", RootCause.OVERDUE_INVOICE),
    ("risk_block", RootCause.RISK_BLOCK),
    ("intl_decline", RootCause.INTRA_DECLINE),
    ("already_paid_delayed_webhook", RootCause.BANK_TIMEOUT),
    ("dispute_filed", RootCause.DISPUTE),
    ("unknown_error", RootCause.UNKNOWN_ERROR),
    ("wrong_person", RootCause.UNKNOWN_ERROR),
    ("adversarial_webhook", RootCause.UNKNOWN_ERROR),
    ("prompt_injection_attempt", RootCause.UNKNOWN_ERROR),
    ("ptp_broken", RootCause.PTP_BROKEN),
)


@pytest.mark.parametrize(("failure_reason", "expected"), FAILURE_CASES)
def test_rules_classify_every_plan_failure_type(
    failure_reason: str, expected: RootCause
) -> None:
    event = {
        "event_type": "payment.failed",
        "failure_reason": failure_reason,
        "error_description": failure_reason,
    }

    verdict = RulesEngine().classify_with_rules(RecoveryCase("case_1", "cus_1"), event)

    assert verdict.applies is True
    assert verdict.root_cause is expected


def test_rules_halt_dispute_and_fraud_cases() -> None:
    rules = RulesEngine()

    disputed = rules.classify_with_rules(
        RecoveryCase("case_dispute", "cus_1", dispute_score=0.91),
        {"failure_reason": "bank_timeout"},
    )
    fraud = rules.classify_with_rules(
        RecoveryCase("case_fraud", "cus_2", fraud_score=0.88),
        {"failure_reason": "bank_timeout"},
    )

    assert disputed.root_cause is RootCause.DISPUTE
    assert fraud.root_cause is RootCause.RISK_BLOCK


def test_mandate_direction_and_terminal_failure_helpers() -> None:
    assert (
        mandate_revocation_direction("customer", "Customer revoked auto debit")
        == "CUSTOMER"
    )
    assert (
        mandate_revocation_direction("bank", "NACH mandate inactive at bank") == "BANK"
    )
    assert mandate_revocation_direction("gateway", "temporary timeout") == "UNKNOWN"
    assert is_terminal_failure("customer", "mandate_revoked_customer") is True
    assert is_terminal_failure("issuer", "bank_timeout temporary") is False


def test_hybrid_uses_rules_before_ml() -> None:
    classifier = HybridClassifier(
        rules=RulesEngine(), ml=ClassificationModel(MODEL_PATH)
    )

    result = classifier.classify(
        RecoveryCase("case_rules", "cus_1"),
        _features_for({"failure_reason": "gateway_error"}),
        {"failure_reason": "dispute_filed"},
    )

    assert result.root_cause is RootCause.DISPUTE
    assert result.sources[0] == "rules"


def test_trained_classifier_artifact_predicts_from_batch_features() -> None:
    rows = _rows_by_failure_reason()
    model = ClassificationModel(MODEL_PATH)

    for failure_reason, expected in FAILURE_CASES:
        if failure_reason == "ptp_broken":
            continue
        prediction = model.predict(rows[failure_reason])
        assert prediction is expected


def _rows_by_failure_reason() -> dict[str, dict[str, str]]:
    with BATCH_PATH.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {row["failure_reason"]: row for row in rows}


def _features_for(overrides: dict[str, object] | None = None) -> RiskFeatures:
    values = {
        "amount_paise": 100000,
        "failure_reason": "bank_timeout",
        "payment_method": "upi",
        "bank": "HDFC",
        "hour_of_day": 10,
        "day_of_week": 1,
        "day_of_month": 1,
        "days_overdue": 0,
        "previous_retry_success": True,
        "previous_dunning_response": "calm",
        "ptp_history_count": 0,
        "customer_segment": "consumer",
        "recent_activity_ts": 1788156780.0,
    }
    values.update(overrides or {})
    return RiskFeatures(**values)
