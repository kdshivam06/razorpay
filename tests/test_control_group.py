"""Tests for measurement and experimentation (§12)."""

from __future__ import annotations

import pandas as pd
import pytest

from app.measurement.control_group import ControlGroup
from app.measurement.counterfactual_sim import CounterfactualSimulator
from app.measurement.drift_detector import DriftDetector
from app.measurement.experiment_engine import Experiment, ExperimentEngine
from app.measurement.model_evaluation import ModelEvaluator


def test_control_split_is_deterministic_exact_90_10_and_disjoint() -> None:
    case_ids = [f"case_{index:03d}" for index in range(100)]
    control = ControlGroup()

    treatment_a, holdout_a = control.split(case_ids)
    treatment_b, holdout_b = control.split(list(reversed(case_ids)))

    assert len(treatment_a) == 90
    assert len(holdout_a) == 10
    assert set(treatment_a).isdisjoint(holdout_a)
    assert sorted(treatment_a + holdout_a) == sorted(case_ids)
    assert treatment_a == treatment_b
    assert holdout_a == holdout_b


def test_assignment_is_stable_and_uses_expected_group_names() -> None:
    control = ControlGroup()

    first = control.assign("case_same_customer_001")
    second = control.assign("case_same_customer_001")

    assert first == second
    assert first.group in {"control", "treatment"}


def test_experiment_engine_records_metrics_and_stops_on_guardrail() -> None:
    engine = ExperimentEngine()
    experiment = Experiment(
        experiment_id="exp_001",
        population="insufficient-funds cases",
        control_group=["case_c_001"],
        treatment_group=["case_t_001", "case_t_002"],
        primary_metric="incremental_recovered_inr",
        guardrails={"complaint_rate": 0.01},
        stop_conditions={"incremental_recovered_inr": {"min": 0.0}},
        results={},
    )

    assert engine.create(experiment) == "exp_001"
    engine.redeem("exp_001", "incremental_recovered_inr", 1200.0)
    assert engine.should_stop("exp_001") is False
    engine.redeem("exp_001", "complaint_rate", 0.02)
    assert engine.should_stop("exp_001") is True


def test_counterfactual_simulator_labels_synthetic_and_selects_best_net_strategy() -> None:
    cases = [
        {
            "case_id": "case_001",
            "outstanding_amount_paise": 100000,
            "true_natural_probability": 0.2,
            "true_action_uplift": {"NO_ACTION": 0.0, "SEND_PAYMENT_LINK": 0.4},
        },
        {
            "case_id": "case_002",
            "outstanding_amount_paise": 200000,
            "ground_truth": {
                "true_natural_probability": 0.5,
                "true_action_uplift": {"NO_ACTION": 0.0, "VOICE_CALL": 0.1},
            },
        },
    ]
    strategies = {
        "Fixed Rules": {
            "contact_rate": 0.6,
            "uplift_multiplier": 0.5,
            "cost_per_contact_paise": 100,
            "base_contacts_per_case": 2,
        },
        "AI Optimized Recovery": {
            "contact_rate": 1.0,
            "uplift_multiplier": 1.0,
            "cost_per_contact_paise": 100,
            "base_contacts_per_case": 1,
        },
        "Human Only": {
            "contact_rate": 1.0,
            "uplift_multiplier": 0.8,
            "cost_per_contact_paise": 50000,
            "base_contacts_per_case": 1,
        },
    }

    comparison = CounterfactualSimulator().run(strategies, cases)

    assert comparison.best_strategy == "AI Optimized Recovery"
    assert comparison.strategies["AI Optimized Recovery"]["synthetic"] is True
    assert comparison.strategies["AI Optimized Recovery"]["incremental_recovered_paise"] > 0
    assert comparison.strategies["Human Only"]["cost_paise"] > comparison.strategies["Fixed Rules"]["cost_paise"]


def test_model_evaluator_reports_classification_probability_and_uplift_metrics() -> None:
    evaluator = ModelEvaluator()

    classification = evaluator.classification(
        pd.Series(["bank_timeout", "expired_card", "expired_card", "risk_block"]),
        pd.Series(["bank_timeout", "expired_card", "bank_timeout", "risk_block"]),
    )
    probability = evaluator.probability(
        pd.Series([0, 0, 1, 1]),
        pd.Series([0.1, 0.2, 0.8, 0.9]),
    )
    uplift = evaluator.uplift(
        pd.Series([1, 1, 0, 0, 1]),
        pd.Series([0.9, 0.8, 0.3, 0.2, 0.1]),
    )

    assert classification.f1 > 0.7
    assert "confusion_matrix" in classification.extra
    assert probability.au_roc == pytest.approx(1.0)
    assert probability.extra["brier_score"] < 0.05
    assert uplift.qini > 0
    assert uplift.incremental_recovered_paise > 0
    assert "0.3" in uplift.extra["uplift_at_k"]


def test_drift_detector_flags_large_behavior_change_without_retraining() -> None:
    signal = DriftDetector().check(baseline=0.25, current=0.09, threshold=0.10)

    assert signal.drifted is True
    assert "MODEL / POLICY DRIFT" in signal.detail
    assert "no automatic retraining" in signal.detail.lower()
