"""Tests for the uplift model."""

from __future__ import annotations

from app.contracts import Action
from app.core.recovery_case import UpliftSegment
from app.revenue_risk.recovery_opportunity import RecoveryOpportunityEngine
from app.revenue_risk.uplift_model import IncrementalUpliftModel, segment_from_estimates


def test_segment_assignment_for_all_four_uplift_groups() -> None:
    assert (
        segment_from_estimates(
            0.86,
            {
                Action.NO_ACTION: 0.0,
                Action.SEND_SMS: 0.03,
                Action.SEND_PAYMENT_LINK: 0.04,
            },
        )
        is UpliftSegment.SURE_THING
    )
    assert (
        segment_from_estimates(
            0.32,
            {
                Action.NO_ACTION: 0.0,
                Action.SEND_SMS: 0.09,
                Action.SEND_PAYMENT_LINK: 0.28,
            },
        )
        is UpliftSegment.PERSUADABLE
    )
    assert (
        segment_from_estimates(
            0.16,
            {
                Action.NO_ACTION: 0.0,
                Action.SEND_SMS: 0.02,
                Action.SEND_PAYMENT_LINK: 0.05,
            },
        )
        is UpliftSegment.LOST_CAUSE
    )
    assert (
        segment_from_estimates(
            0.62,
            {
                Action.NO_ACTION: 0.0,
                Action.SEND_SMS: -0.08,
                Action.SEND_PAYMENT_LINK: -0.04,
                Action.VOICE_CALL: -0.16,
            },
        )
        is UpliftSegment.SLEEPING_DOG
    )


def test_no_action_is_favored_for_sure_thing_segment() -> None:
    model = IncrementalUpliftModel()
    features = _features(
        failure_reason="already_paid_delayed_webhook",
        previous_retry_success=True,
        amount_paise=100000,
        days_overdue=0,
        ptp_history_count=0,
    )

    estimates = model.estimates(features)

    assert estimates.uplift_segment is UpliftSegment.SURE_THING
    assert model.best_action(features) is Action.NO_ACTION


def test_no_action_is_favored_for_sleeping_dog_segment() -> None:
    model = IncrementalUpliftModel()
    features = _features(
        failure_reason="risk_block",
        previous_retry_success=False,
        amount_paise=900000,
        days_overdue=4,
        ptp_history_count=2,
    )

    estimates = model.estimates(features)

    assert estimates.uplift_segment is UpliftSegment.SLEEPING_DOG
    assert model.best_action(features) is Action.NO_ACTION
    assert min(estimates.per_action_uplift.values()) < 0


def test_persuadable_segment_selects_incremental_action() -> None:
    model = IncrementalUpliftModel()
    features = _features(
        failure_reason="expired_card",
        channel_preference="PAYMENT_LINK",
        previous_retry_success=False,
        amount_paise=350000,
        days_overdue=1,
    )

    estimates = model.estimates(features)
    best = model.best_action(features)

    assert estimates.uplift_segment is UpliftSegment.PERSUADABLE
    assert best is Action.REQUEST_PAYMENT_METHOD_UPDATE
    assert estimates.per_action_uplift[best] > 0.08


def test_recovery_opportunity_uses_positive_incremental_uplift_only() -> None:
    engine = RecoveryOpportunityEngine()

    opportunities = engine.opportunity_per_action(
        100000,
        {
            Action.SEND_SMS: 0.09,
            Action.SEND_PAYMENT_LINK: 0.22,
            Action.VOICE_CALL: -0.04,
        },
    )
    best_action, best_value = engine.best_incremental_action(
        100000,
        {
            Action.SEND_SMS: 0.09,
            Action.SEND_PAYMENT_LINK: 0.22,
            Action.VOICE_CALL: -0.04,
        },
    )

    assert opportunities[Action.VOICE_CALL] == 0
    assert best_action is Action.SEND_PAYMENT_LINK
    assert best_value == 22000


def _features(**overrides: object) -> dict[str, object]:
    features = {
        "amount_paise": 200000,
        "failure_reason": "insufficient_funds_low",
        "payment_method": "upi",
        "bank": "HDFC",
        "hour_of_day": 18,
        "day_of_week": 1,
        "day_of_month": 1,
        "days_overdue": 2,
        "previous_retry_success": False,
        "previous_dunning_response": "calm",
        "ptp_history_count": 0,
        "customer_segment": "consumer",
        "recent_activity_ts": 1788156780.0,
        "channel_preference": "SMS",
        "persona": "P6",
    }
    features.update(overrides)
    return features
