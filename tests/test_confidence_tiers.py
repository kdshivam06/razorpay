"""Track F.2 — configurable confidence ladder in InterventionOptimizer.

Verifies:
  - The ladder is driven ONLY by the trained propensity model's real
    predict_proba output for the specific case (identity of the model + the
    exact probability are carried on the recommendation) — never invented.
  - Band routing: HIGH → SMS/WhatsApp reminder + payment link; MEDIUM → auto
    AI voice nudge (E.8); EMERGING → channel chosen via affinity/fatigue;
    HUMAN_REVIEW (< low) → human. UNKNOWN → no model consulted → legacy
    selection (no invented number, no forced human in legacy mode).
  - SLEEPING_DOG overrides any band → human review (NO_ACTION proposed).
  - Thresholds come from recovery_config.yaml (named, commented, tunable);
    invalid config falls back to spec defaults.
  - The decision packet exposes both the tier and the exact probability.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.audit.decision_trace import DecisionTracer
from app.contracts import Action, CandidateAction
from app.core.obligation import Obligation
from app.core.recovery_case import RecoveryCase, UpliftSegment
from app.dashboard.case_inspector import CaseInspector, packet_to_dict
from app.optimizer.intervention_optimizer import (
    ConfidenceLadder,
    ConfidenceTier,
    InterventionOptimizer,
)
from app.revenue_risk.risk_features import RiskFeatures
from app.revenue_risk.uplift_model import UpliftEstimates

# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

SPEC_LADDER = ConfidenceLadder(
    high_threshold=0.80, medium_threshold=0.70, low_threshold=0.60
)

_BASE_UPLIFTS = {
    Action.NO_ACTION: 0.0,
    Action.WAIT: 0.02,
    Action.RETRY_SAME_METHOD: 0.05,
    Action.RETRY_ALTERNATE_METHOD: 0.04,
    Action.SEND_PAYMENT_LINK: 0.37,
    Action.SEND_SMS: 0.12,
    Action.SEND_EMAIL: 0.08,
    Action.SEND_WHATSAPP: 0.15,
    Action.VOICE_CALL: 0.30,
    Action.REQUEST_PAYMENT_METHOD_UPDATE: 0.08,
    Action.OFFER_PARTIAL_PAYMENT: 0.10,
    Action.CREATE_PTP: 0.06,
    Action.HUMAN_ESCALATION: 0.03,
    Action.BLOCK: 0.0,
}


def _case(
    segment: UpliftSegment = UpliftSegment.PERSUADABLE, natural: float = 0.3
) -> RecoveryCase:
    rc = RecoveryCase(
        case_id="RC_TIER", customer_id="CUST_TIER", natural_pay_probability=natural
    )
    rc.add_obligation(
        Obligation(obligation_id="OBL_1", type="payment", original_amount=5_000_000)
    )
    return rc


def _estimates(
    baseline: float = 0.3,
    segment: UpliftSegment = UpliftSegment.PERSUADABLE,
    uplifts: dict[Action, float] | None = None,
) -> UpliftEstimates:
    profile = dict(_BASE_UPLIFTS)
    if uplifts:
        profile.update(uplifts)
    return UpliftEstimates(
        baseline_natural_probability=baseline,
        per_action_probability={a: baseline + u for a, u in profile.items()},
        per_action_uplift=profile,
        uplift_segment=segment,
    )


def _features(
    reason: str, *, amount: int = 500_000, days: int = 1, prev: bool = False
) -> RiskFeatures:
    return RiskFeatures(
        amount_paise=amount,
        failure_reason=reason,
        payment_method="upi",
        bank="",
        hour_of_day=11,
        day_of_week=1,
        day_of_month=2,
        days_overdue=days,
        previous_retry_success=prev,
        previous_dunning_response="",
        ptp_history_count=0,
        customer_segment="P2",
        recent_activity_ts=1.0,
    )


def _candidate(action: Action, score: float) -> CandidateAction:
    return CandidateAction(
        action=action,
        expected_recovery_paise=0,
        communication_cost_paise=0,
        operational_cost_paise=0,
        risk_penalty_paise=0,
        cx_penalty_paise=0,
        economic_score=score,
    )


# ──────────────────────────────────────────────────────────────────────
# Spec-band ladder (0.60 / 0.70 / 0.80) — one case per band
# ──────────────────────────────────────────────────────────────────────


class TestSpecBandRouting:
    def test_high_band_auto_reminder_link(self):
        """≥0.80 → SMS/WhatsApp reminder + payment link, fully automatic."""
        opt = InterventionOptimizer(ladder=SPEC_LADDER)
        # already_paid_delayed_webhook + prior success → 0.9616
        rec = opt.optimize(
            _case(), _estimates(), features=_features("already_paid_delayed_webhook", prev=True)
        )
        assert rec.confidence_tier == ConfidenceTier.HIGH.value
        assert rec.confidence_probability == pytest.approx(0.9616, abs=0.005)
        assert rec.requires_human_approval is False
        assert rec.selected_action in {
            Action.SEND_SMS, Action.SEND_WHATSAPP, Action.SEND_PAYMENT_LINK,
        }
        # Voice has the highest economic score yet must NOT win in HIGH.
        assert rec.selected_action != Action.VOICE_CALL

    def test_medium_band_auto_voice_nudge(self):
        """0.70–0.80 → auto AI voice nudge via the E.8 voice agent."""
        opt = InterventionOptimizer(ladder=SPEC_LADDER)
        # bank_timeout + prior success → 0.7706
        rec = opt.optimize(_case(), _estimates(), features=_features("bank_timeout", prev=True))
        assert rec.confidence_tier == ConfidenceTier.MEDIUM.value
        assert 0.70 <= rec.confidence_probability < 0.80
        assert rec.requires_human_approval is False
        assert rec.selected_action == Action.VOICE_CALL
        assert "voice nudge" in rec.reasoning.lower()

    def test_emerging_band_uses_affinity_channel(self):
        """0.60–0.70 → auto, channel chosen via channel_affinity + fatigue."""
        from app.optimizer.channel_affinity import ChannelAffinityModel, ChannelAffinityStore
        from app.contracts import ContactChannel

        store = ChannelAffinityStore()
        for _ in range(5):
            store.record("CUST_TIER", ContactChannel.WHATSAPP, clicked=True, opened=True, latency_s=0.0)
        affinity = ChannelAffinityModel(store=store)
        opt = InterventionOptimizer(ladder=SPEC_LADDER, affinity=affinity)
        # gateway_error → 0.6772 (EMERGING)
        rec = opt.optimize(_case(), _estimates(), features=_features("gateway_error"))
        assert rec.confidence_tier == ConfidenceTier.EMERGING.value
        assert 0.60 <= rec.confidence_probability < 0.70
        assert rec.requires_human_approval is False
        # Voice has the highest economic score, but WhatsApp is the customer's
        # preferred conversion channel → the affinity channel wins.
        assert rec.selected_action == Action.SEND_WHATSAPP

    def test_human_review_band_routes_to_human(self):
        """<0.60 (spec low) → human review, flagged with tier + source."""
        opt = InterventionOptimizer(ladder=SPEC_LADDER)
        # risk_block → 0.0494
        rec = opt.optimize(_case(), _estimates(), features=_features("risk_block"))
        assert rec.confidence_tier == ConfidenceTier.HUMAN_REVIEW.value
        assert rec.confidence_probability < 0.60
        assert rec.requires_human_approval is True
        assert rec.confidence_source == "propensity_v1"
        assert "routed to human review" in rec.reasoning.lower()


# ──────────────────────────────────────────────────────────────────────
# SLEEPING_DOG override + UNKNOWN (no model)
# ──────────────────────────────────────────────────────────────────────


class TestOverrideAndUnknown:
    def test_sleeping_dog_overrides_to_human_even_in_high_band(self):
        """SLEEPING_DOG → NO_ACTION proposed, human review forced."""
        opt = InterventionOptimizer(ladder=SPEC_LADDER)
        rec = opt.optimize(
            _case(segment=UpliftSegment.SLEEPING_DOG),
            _estimates(segment=UpliftSegment.SLEEPING_DOG),
            features=_features("already_paid_delayed_webhook", prev=True),
        )
        assert rec.selected_action == Action.NO_ACTION
        assert rec.requires_human_approval is True
        assert rec.confidence_tier == ConfidenceTier.HUMAN_REVIEW.value

    def test_unarmed_unknown_never_invents_a_number(self):
        """No features → confidence None + UNKNOWN tier; legacy semantics kept."""
        opt = InterventionOptimizer(ladder=SPEC_LADDER)
        rec = opt.optimize(_case(), _estimates())
        assert rec.confidence_probability is None
        assert rec.confidence_tier == ConfidenceTier.UNKNOWN.value
        assert rec.confidence_source is None
        # Legacy: low-risk case stays autonomous; selection unchanged.
        assert rec.selected_action == Action.SEND_PAYMENT_LINK
        assert rec.requires_human_approval is False
        assert "Confidence tier" not in rec.reasoning


# ──────────────────────────────────────────────────────────────────────
# recovery_config.yaml drives the thresholds (tunability)
# ──────────────────────────────────────────────────────────────────────


class TestConfigDrivenLadder:
    def test_load_reads_yaml_profile(self):
        ladder = ConfidenceLadder.load()
        assert ladder == ConfidenceLadder(
            high_threshold=0.60, medium_threshold=0.50, low_threshold=0.35
        )

    def test_active_emerging_band_from_yaml(self):
        """subscription_pending → 0.357 → EMERGING with the tuned config."""
        opt = InterventionOptimizer()  # loads app/recovery_config.yaml
        rec = opt.optimize(_case(), _estimates(), features=_features("subscription_pending"))
        assert 0.35 <= rec.confidence_probability < 0.50
        assert rec.confidence_tier == ConfidenceTier.EMERGING.value
        assert rec.requires_human_approval is False

    def test_active_medium_band_from_yaml(self):
        """partial_payment → 0.577 → MEDIUM with the tuned config."""
        opt = InterventionOptimizer()
        rec = opt.optimize(_case(), _estimates(), features=_features("partial_payment"))
        assert 0.50 <= rec.confidence_probability < 0.60
        assert rec.confidence_tier == ConfidenceTier.MEDIUM.value
        assert rec.selected_action == Action.VOICE_CALL
        assert rec.requires_human_approval is False

    def test_active_human_band_from_yaml(self):
        """risk_block → 0.049 → HUMAN_REVIEW below the tuned low threshold."""
        opt = InterventionOptimizer()
        rec = opt.optimize(_case(), _estimates(), features=_features("risk_block"))
        assert rec.confidence_tier == ConfidenceTier.HUMAN_REVIEW.value
        assert rec.requires_human_approval is True

    def test_hard_safety_flag_overrides_high_band(self):
        """Fraud/blast/reversibility flags override even a HIGH-confidence band."""
        from app.policy.fraud_detector import FraudDetector

        opt = InterventionOptimizer(
            ladder=SPEC_LADDER, fraud_detector=FraudDetector(thresholds={"high": 0.3})
        )
        case = _case()
        case.fraud_score = 0.5
        rec = opt.optimize(
            case, _estimates(), features=_features("already_paid_delayed_webhook", prev=True)
        )
        assert rec.confidence_tier == ConfidenceTier.HUMAN_REVIEW.value
        assert rec.requires_human_approval is True

    def test_invalid_config_falls_back_to_spec_defaults(self, tmp_path: Path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "confidence_ladder:\n  high_threshold: 0.2\n  medium_threshold: 0.9\n"
            "  low_threshold: 0.7\n",
            encoding="utf-8",
        )
        assert ConfidenceLadder.load(str(bad)) == ConfidenceLadder()  # spec defaults


# ──────────────────────────────────────────────────────────────────────
# Decision packet exposes tier + exact probability (E.1 API update)
# ──────────────────────────────────────────────────────────────────────


class TestDecisionPacketExposure:
    def test_packet_carries_confidence_fields(self):
        tracer = DecisionTracer()
        candidates = [
            _candidate(Action.SEND_PAYMENT_LINK, 0.45),
            _candidate(Action.VOICE_CALL, 0.35),
            _candidate(Action.SEND_SMS, 0.25),
            _candidate(Action.NO_ACTION, 0.0),
        ]
        tracer.build(
            case_id="RC_TIER_PACKET",
            trigger_event="payment.failed",
            state="RISK_ASSESSED",
            root_cause="bank_timeout",
            natural_payment_probability=0.70,
            uplift_segment="PERSUADABLE",
            revenue_at_risk_paise=5_000_000,
            candidate_actions=candidates,
            selected_action=Action.SEND_PAYMENT_LINK,
            selected_economic_score=0.45,
            policy_gate_result="APPROVED",
            requires_human_approval=False,
            reasoning="Reminder + payment link for high model confidence.",
            confidence_probability=0.9616,
            confidence_tier=ConfidenceTier.HIGH.value,
            confidence_source="propensity_v1",
        )
        inspector = CaseInspector(tracer=tracer)
        packet = inspector.build_packet("RC_TIER_PACKET")
        assert packet is not None
        assert packet.recommendation.confidence_probability == 0.9616
        assert packet.recommendation.confidence_tier == ConfidenceTier.HIGH.value
        assert packet.recommendation.confidence_source == "propensity_v1"
        # The UI confidence value is now the model's real probability.
        assert packet.recommendation.confidence == 0.9616

        data = packet_to_dict(packet)
        rec = data["recommendation"]
        assert rec["confidence_probability"] == 0.9616
        assert rec["confidence_tier"] == ConfidenceTier.HIGH.value
        assert rec["confidence_source"] == "propensity_v1"