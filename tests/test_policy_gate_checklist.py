"""Test suite: Policy Gate Checklist — every gate reports a result.

Asserts that PolicyEngine.evaluate() returns a GateResult for every gate
in the chain, with result in {PASS, FAIL, BLOCKED, SKIPPED} and a legal_basis.
No gate is silently dropped.
"""

import pytest

from app.contracts import Action
from app.core.recovery_case import RecoveryCase, UpliftSegment
from app.core.obligation import Obligation, ObligationStatus
from app.policy.policy_engine import PolicyEngine, GateResult
from app.policy.legal_basis import get_legal_basis, STATUTORY_GATES, INTERNAL_GATES, all_gates


class TestPolicyGateChecklist:
    """Every gate must report a result with legal basis."""

    def setup_method(self):
        self.engine = PolicyEngine()
        self.case = self._make_case()

    def _make_case(self) -> RecoveryCase:
        """Create a test case with obligations."""
        case = RecoveryCase(
            case_id="RC_TEST",
            customer_id="CUST_TEST",
            root_cause="insufficient_funds",
            natural_pay_probability=0.3,
            uplift_segment=UpliftSegment.PERSUADABLE,
        )
        obl = Obligation(
            obligation_id="OBL_TEST",
            type="payment",
            original_amount=500_000,
            customer_id="CUST_TEST",
            status=ObligationStatus.OPEN,
        )
        case.add_obligation(obl)
        return case

    def test_all_gates_present_in_evaluation(self):
        """evaluate() returns a GateResult for every gate in the chain."""
        evaluation = self.engine.evaluate(self.case, Action.SEND_SMS)
        
        gate_names = {gr.gate_name for gr in evaluation.gate_results}
        
        # All 9 gates + passthrough should be present
        expected_gates = {
            "consent", "contact_window", "customer_preference", "cooldown",
            "fraud", "dispute", "reversibility", "blast_radius", "platform_awareness"
        }
        # passthrough only for NO_ACTION/WAIT/BLOCK
        assert expected_gates.issubset(gate_names), f"Missing gates: {expected_gates - gate_names}"

    def test_every_gate_has_result(self):
        """Every gate result is PASS, FAIL, BLOCKED, or SKIPPED — never missing."""
        evaluation = self.engine.evaluate(self.case, Action.SEND_SMS)
        
        for gate in evaluation.gate_results:
            assert gate.result in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}, \
                f"Gate {gate.gate_name} has invalid result: {gate.result}"
            assert gate.gate_name, "Gate name must not be empty"

    def test_every_gate_has_legal_basis(self):
        """Every gate result includes legal_basis and description."""
        evaluation = self.engine.evaluate(self.case, Action.SEND_SMS)
        
        for gate in evaluation.gate_results:
            assert gate.legal_basis, f"Gate {gate.gate_name} missing legal_basis"
            assert gate.legal_basis_description, f"Gate {gate.gate_name} missing legal_basis_description"
            assert gate.legal_basis != "unknown", f"Gate {gate.gate_name} has unknown legal_basis"

    def test_statutory_gates_marked_correctly(self):
        """Gates with statutory basis are correctly identified."""
        evaluation = self.engine.evaluate(self.case, Action.SEND_SMS)
        
        statutory_found = {gr.gate_name for gr in evaluation.gate_results if gr.legal_basis != "internal_policy"}
        # consent, contact_window, dispute are statutory
        assert "consent" in statutory_found
        assert "contact_window" in statutory_found
        assert "dispute" in statutory_found

    def test_internal_gates_marked_internal(self):
        """Internal policy gates have legal_basis = 'internal_policy'."""
        evaluation = self.engine.evaluate(self.case, Action.SEND_SMS)
        
        for gate in evaluation.gate_results:
            if gate.gate_name in INTERNAL_GATES:
                assert gate.legal_basis == "internal_policy", \
                    f"Gate {gate.gate_name} should be internal_policy, got {gate.legal_basis}"

    def test_gate_result_includes_reason_for_fail(self):
        """FAIL/BLOCKED gates include a human-readable reason."""
        # Create a case that will fail consent gate
        case = self._make_case()
        # Don't configure consent manager - default denies consent
        
        evaluation = self.engine.evaluate(case, Action.SEND_SMS)
        
        consent_gate = next(gr for gr in evaluation.gate_results if gr.gate_name == "consent")
        # Default ConsentManager returns False for has_consent
        assert consent_gate.result == "FAIL"
        assert consent_gate.reason is not None
        assert "TRAI" in consent_gate.reason or "consent" in consent_gate.reason.lower()

    def test_skipped_gates_for_non_outbound_actions(self):
        """Non-outbound actions have consent/window/preference/cooldown SKIPPED."""
        evaluation = self.engine.evaluate(self.case, Action.NO_ACTION)
        
        for gate_name in ["consent", "contact_window", "customer_preference", "cooldown"]:
            gate = next(gr for gr in evaluation.gate_results if gr.gate_name == gate_name)
            assert gate.result == "SKIPPED", f"Gate {gate_name} should be SKIPPED for NO_ACTION"
            assert gate.reason is not None

    def test_passthrough_gate_for_internal_actions(self):
        """NO_ACTION/WAIT/BLOCK get passthrough gate."""
        for action in [Action.NO_ACTION, Action.WAIT, Action.BLOCK]:
            evaluation = self.engine.evaluate(self.case, action)
            passthrough = next(gr for gr in evaluation.gate_results if gr.gate_name == "passthrough")
            assert passthrough.result == "PASS"
            assert passthrough.reason is not None

    def test_blast_radius_skipped_for_non_rate_actions(self):
        """NO_ACTION has blast_radius SKIPPED."""
        evaluation = self.engine.evaluate(self.case, Action.NO_ACTION)
        blast = next(gr for gr in evaluation.gate_results if gr.gate_name == "blast_radius")
        assert blast.result == "SKIPPED"

    def test_legal_basis_registry_completeness(self):
        """All gates in the engine have entries in legal_basis registry."""
        registered = set(all_gates().keys())
        # All gates that can appear in evaluation
        possible_gates = {
            "consent", "contact_window", "customer_preference", "cooldown",
            "fraud", "dispute", "reversibility", "blast_radius", "platform_awareness", "passthrough"
        }
        assert possible_gates.issubset(registered), f"Missing registry entries: {possible_gates - registered}"

    def test_no_duplicate_gate_names(self):
        """No duplicate gate names in a single evaluation."""
        evaluation = self.engine.evaluate(self.case, Action.SEND_SMS)
        gate_names = [gr.gate_name for gr in evaluation.gate_results]
        assert len(gate_names) == len(set(gate_names)), "Duplicate gate names found"

    def test_fraud_gate_always_runs(self):
        """Fraud gate runs for every action (PASS/FAIL, never SKIPPED except passthrough)."""
        for action in Action:
            evaluation = self.engine.evaluate(self.case, action)
            fraud_gate = next(gr for gr in evaluation.gate_results if gr.gate_name == "fraud")
            if action in [Action.NO_ACTION, Action.WAIT, Action.BLOCK]:
                assert fraud_gate.result == "SKIPPED"  # Passthrough actions skip all
            else:
                assert fraud_gate.result in {"PASS", "FAIL"}
            assert fraud_gate.legal_basis == "internal_policy"

    def test_dispute_gate_always_runs(self):
        """Dispute gate runs for every action (PASS/FAIL, never SKIPPED except passthrough)."""
        for action in Action:
            evaluation = self.engine.evaluate(self.case, action)
            dispute_gate = next(gr for gr in evaluation.gate_results if gr.gate_name == "dispute")
            if action in [Action.NO_ACTION, Action.WAIT, Action.BLOCK]:
                assert dispute_gate.result == "SKIPPED"  # Passthrough actions skip all
            else:
                assert dispute_gate.result in {"PASS", "FAIL"}
            assert dispute_gate.legal_basis != "internal_policy"  # Statutory

    def test_reversibility_gate_always_runs(self):
        """Reversibility gate runs for every action (PASS/FAIL, never SKIPPED except passthrough)."""
        for action in Action:
            evaluation = self.engine.evaluate(self.case, action)
            rev_gate = next(gr for gr in evaluation.gate_results if gr.gate_name == "reversibility")
            if action in [Action.NO_ACTION, Action.WAIT, Action.BLOCK]:
                assert rev_gate.result == "SKIPPED"  # Passthrough actions skip all
            else:
                assert rev_gate.result in {"PASS", "FAIL"}
            assert rev_gate.legal_basis == "internal_policy"


class TestPolicyGateLegalBasisRegistry:
    """Tests for the legal_basis module."""

    def test_all_known_gates_registered(self):
        """All expected gates have legal basis entries."""
        gates = all_gates()
        expected = {
            "consent", "contact_window", "customer_preference", "cooldown",
            "fraud", "dispute", "reversibility", "blast_radius",
            "platform_awareness", "passthrough"
        }
        assert expected.issubset(set(gates.keys()))

    def test_statutory_gates_not_internal(self):
        """Statutory gates have non-internal legal basis."""
        for gate in STATUTORY_GATES:
            legal_basis, _ = get_legal_basis(gate)
            assert legal_basis != "internal_policy", f"Statutory gate {gate} marked internal"

    def test_internal_gates_marked_internal(self):
        """Internal gates have internal_policy legal basis."""
        for gate in INTERNAL_GATES:
            legal_basis, _ = get_legal_basis(gate)
            assert legal_basis == "internal_policy", f"Internal gate {gate} not marked internal"

    def test_descriptions_non_empty(self):
        """All descriptions are non-empty strings."""
        for gate_name, (basis, desc) in all_gates().items():
            assert isinstance(desc, str) and len(desc) > 10, f"Gate {gate_name} has empty/short description"

    def test_unknown_gate_returns_fallback(self):
        """Unknown gate returns fallback."""
        basis, desc = get_legal_basis("nonexistent_gate_xyz")
        assert basis == "unknown"
        assert "No legal basis registered" in desc


class TestPolicyGateIntegration:
    """Integration tests with real gate components."""

    def setup_method(self):
        self.engine = PolicyEngine()

    def test_blocked_action_includes_all_failed_gates(self):
        """When action is BLOCKED, all failed gates have FAIL result."""
        case = RecoveryCase(
            case_id="RC_BLOCKED",
            customer_id="CUST_BLOCKED",
            root_cause="dispute",
            natural_pay_probability=0.1,
            uplift_segment=UpliftSegment.SLEEPING_DOG,
        )
        obl = Obligation(
            obligation_id="OBL_DISP",
            type="payment",
            original_amount=100_000,
            customer_id="CUST_BLOCKED",
            status=ObligationStatus.DISPUTED,
        )
        case.add_obligation(obl)

        evaluation = self.engine.evaluate(case, Action.SEND_SMS)
        
        assert evaluation.result.value == "BLOCKED"
        
        # Dispute gate should be FAIL
        dispute_gate = next(gr for gr in evaluation.gate_results if gr.gate_name == "dispute")
        assert dispute_gate.result == "FAIL"
        assert "dispute" in dispute_gate.reason.lower()
        
        # All other gates still present
        gate_names = {gr.gate_name for gr in evaluation.gate_results}
        assert len(gate_names) >= 9

    def test_high_value_action_requires_human(self):
        """High-value financial actions flag human approval via reversibility gate."""
        case = RecoveryCase(
            case_id="RC_HIGH",
            customer_id="CUST_HIGH",
            root_cause="insufficient_funds",
            natural_pay_probability=0.3,
            uplift_segment=UpliftSegment.PERSUADABLE,
        )
        obl = Obligation(
            obligation_id="OBL_HIGH",
            type="payment",
            original_amount=2_000_000_00,  # ₹2L > ₹1L threshold
            customer_id="CUST_HIGH",
            status=ObligationStatus.OPEN,
        )
        case.add_obligation(obl)

        evaluation = self.engine.evaluate(case, Action.SEND_PAYMENT_LINK)
        
        rev_gate = next(gr for gr in evaluation.gate_results if gr.gate_name == "reversibility")
        assert rev_gate.result == "PASS"
        assert rev_gate.reason is not None
        assert "human approval" in rev_gate.reason.lower() or "requires human" in rev_gate.reason.lower()

    def test_consent_failure_blocks_action(self):
        """Missing consent blocks outbound action."""
        case = RecoveryCase(
            case_id="RC_NOCONSENT",
            customer_id="CUST_NOCONSENT",  # No consent configured
            root_cause="insufficient_funds",
            natural_pay_probability=0.3,
            uplift_segment=UpliftSegment.PERSUADABLE,
        )
        obl = Obligation(
            obligation_id="OBL_NC",
            type="payment",
            original_amount=100_000,
            customer_id="CUST_NOCONSENT",
            status=ObligationStatus.OPEN,
        )
        case.add_obligation(obl)

        evaluation = self.engine.evaluate(case, Action.SEND_SMS)
        
        assert evaluation.result.value == "BLOCKED"
        consent_gate = next(gr for gr in evaluation.gate_results if gr.gate_name == "consent")
        assert consent_gate.result == "FAIL"

    def test_gate_count_consistency(self):
        """Every evaluation returns consistent number of gates based on action type and configured engines."""
        # The default PolicyEngine doesn't configure preferences or cooldown engines,
        # so those gates are SKIPPED. Count varies by configuration.
        # Just verify we get at least 9 gates for all actions (no silent drops).
        for action in Action:
            case = self._make_case_for_action(action)
            evaluation = self.engine.evaluate(case, action)
            
            # Every action should get at least 9 gate results (no silent drops)
            assert len(evaluation.gate_results) >= 9, \
                f"Action {action.value} has {len(evaluation.gate_results)} gates, expected >= 9"
            
            # Passthrough actions should have exactly 10 (1 active + 9 SKIPPED)
            if action in {Action.NO_ACTION, Action.WAIT, Action.BLOCK}:
                assert len(evaluation.gate_results) == 10, \
                    f"Passthrough action {action.value} should have 10 gates"

    def _make_case_for_action(self, action: Action) -> RecoveryCase:
        case = RecoveryCase(
            case_id=f"RC_{action.value}",
            customer_id="CUST_TEST",
            root_cause="insufficient_funds",
            natural_pay_probability=0.3,
            uplift_segment=UpliftSegment.PERSUADABLE,
        )
        obl = Obligation(
            obligation_id=f"OBL_{action.value}",
            type="payment",
            original_amount=500_000,
            customer_id="CUST_TEST",
            status=ObligationStatus.OPEN,
        )
        case.add_obligation(obl)
        return case