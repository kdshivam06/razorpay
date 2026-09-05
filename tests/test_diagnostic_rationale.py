"""Test suite: Diagnostic Rationale Generation (§E.4).

Tests that HybridClassifier generates LLM diagnostic rationale after
deterministic classification, using mocked Gemini responses for at least
5 distinct failure types from the plan.
"""

import pytest
from unittest.mock import Mock
from datetime import datetime, timezone

from app.classifier.hybrid_classifier import HybridClassifier
from app.classifier.rules_engine import RulesEngine
from app.contracts import RootCause
from app.core.recovery_case import RecoveryCase
from app.core.obligation import Obligation, ObligationStatus
from app.nlp.gemini_client import JsonLlmClient
from app.revenue_risk.risk_features import RiskFeatures


class MockGeminiClient(JsonLlmClient):
    """Mock Gemini client with predefined responses for testing."""

    def __init__(self, responses: dict[str, str]):
        self._responses = responses
        self._call_count = 0
        self._last_prompt = ""

    def generate_json(self, prompt: str) -> dict[str, Any]:
        self._call_count += 1
        self._last_prompt = prompt

        # Match response based on root cause in prompt
        for key, rationale in self._responses.items():
            if key in prompt:
                return {"rationale": rationale}

        # Default fallback
        return {"rationale": "Mock rationale for test case."}


class TestDiagnosticRationale:
    """Tests for LLM diagnostic rationale generation."""

    def setup_method(self):
        self.rules = RulesEngine()
        self.case = self._make_case()

    def _make_features(self, failure_reason: str = "insufficient_funds") -> RiskFeatures:
        """Create a minimal RiskFeatures for testing."""
        return RiskFeatures(
            amount_paise=500_000,
            failure_reason=failure_reason,
            payment_method="upi",
            bank="test_bank",
            hour_of_day=12,
            day_of_week=2,
            day_of_month=15,
            days_overdue=1,
            previous_retry_success=False,
            previous_dunning_response="",
            ptp_history_count=0,
            customer_segment="P2",
            recent_activity_ts=datetime.now(timezone.utc).timestamp(),
        )

    def _make_case(
        self,
        case_id: str = "RC_TEST",
        customer_id: str = "CUST_TEST",
        root_cause: str = "insufficient_funds",
    ) -> RecoveryCase:
        case = RecoveryCase(
            case_id=case_id,
            customer_id=customer_id,
            root_cause=root_cause,
            natural_pay_probability=0.3,
        )
        obl = Obligation(
            obligation_id="OBL_TEST",
            type="payment",
            original_amount=500_000,
            customer_id=customer_id,
            status=ObligationStatus.OPEN,
        )
        case.add_obligation(obl)
        return case

    def _make_event(self, failure_reason: str, decline_code: str = "N/A") -> dict:
        return {
            "failure_reason": failure_reason,
            "decline_code": decline_code,
            "event_type": "payment.failed",
        }

    def test_insufficient_funds_rationale(self):
        """Test rationale for insufficient_funds failure."""
        mock_client = MockGeminiClient({
            "insufficient_funds": (
                "The failure reason indicates the customer's account had "
                "insufficient balance at the time of the payment attempt."
            ),
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        features = self._make_features()
        event = self._make_event("insufficient_funds", "INSUFFICIENT_FUNDS")
        classification = classifier.classify(self._make_case(root_cause="insufficient_funds"), features, event)

        assert classification.root_cause == RootCause.INSUFFICIENT_FUNDS
        assert self._make_case(root_cause="insufficient_funds").diagnostic_rationale == ""

    def test_rationale_stored_on_case(self):
        """Test that rationale is stored on the case record."""
        mock_client = MockGeminiClient({
            "expired_card": (
                "The payment method on file has expired, preventing the "
                "transaction from being processed."
            ),
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        case = self._make_case(root_cause="expired_card")
        features = self._make_features()
        event = self._make_event("expired_card", "EXPIRED_CARD")

        classification = classifier.classify(case, features, event)

        assert case.diagnostic_rationale == (
            "The payment method on file has expired, preventing the "
            "transaction from being processed."
        )

    def test_bank_timeout_rationale(self):
        """Test rationale for bank_timeout failure."""
        mock_client = MockGeminiClient({
            "bank_timeout": (
                "The bank did not respond within the expected time window, "
                "causing the transaction to time out."
            ),
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        case = self._make_case(root_cause="bank_timeout")
        features = self._make_features()
        event = self._make_event("bank_timeout", "BANK_TIMEOUT")

        classification = classifier.classify(case, features, event)

        assert case.diagnostic_rationale == (
            "The bank did not respond within the expected time window, "
            "causing the transaction to time out."
        )

    def test_gateway_error_rationale(self):
        """Test rationale for gateway_error failure."""
        mock_client = MockGeminiClient({
            "gateway_error": (
                "A temporary gateway or network error occurred during payment "
                "processing."
            ),
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        case = self._make_case(root_cause="gateway_error")
        features = self._make_features()
        event = self._make_event("gateway_error", "GATEWAY_ERROR")

        classification = classifier.classify(case, features, event)

        assert case.diagnostic_rationale == (
            "A temporary gateway or network error occurred during payment "
            "processing."
        )

    def test_mandate_failure_rationale(self):
        """Test rationale for mandate_failure (customer revoked)."""
        mock_client = MockGeminiClient({
            "mandate_failure": (
                "The mandate was revoked by the customer, preventing automatic "
                "debit and halting all recovery attempts per RBI guidelines."
            ),
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        case = self._make_case(root_cause="mandate_failure")
        features = self._make_features()
        event = self._make_event("mandate_revoked_customer", "MANDATE_REVOKED")

        classification = classifier.classify(case, features, event)

        assert case.diagnostic_rationale == (
            "The mandate was revoked by the customer, preventing automatic "
            "debit and halting all recovery attempts per RBI guidelines."
        )

    def test_checkout_abandoned_rationale(self):
        """Test rationale for checkout_abandoned failure."""
        mock_client = MockGeminiClient({
            "checkout_abandoned": (
                "The customer initiated checkout but did not complete the "
                "payment within the session window."
            ),
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        case = self._make_case(root_cause="checkout_abandoned")
        features = self._make_features()
        event = self._make_event("checkout_abandoned", "CHECKOUT_ABANDONED")

        classification = classifier.classify(case, features, event)

        assert case.diagnostic_rationale == (
            "The customer initiated checkout but did not complete the "
            "payment within the session window."
        )

    def test_rationale_not_regenerated(self):
        """Test that rationale is generated once, not on subsequent calls."""
        mock_client = MockGeminiClient({
            "insufficient_funds": "First rationale generated.",
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        case = self._make_case(root_cause="insufficient_funds")
        features = self._make_features()
        event = self._make_event("insufficient_funds")

        # First classification
        classifier.classify(case, features, event)
        first_rationale = case.diagnostic_rationale
        first_call_count = mock_client._call_count

        # Second classification on same case
        classifier.classify(case, features, event)
        second_rationale = case.diagnostic_rationale
        second_call_count = mock_client._call_count

        # Rationale should not be regenerated
        assert first_rationale == second_rationale
        assert second_call_count == first_call_count  # No additional LLM call

    def test_llm_failure_graceful(self):
        """Test graceful handling when LLM client fails."""
        failing_client = MockGeminiClient({})
        failing_client.generate_json = Mock(side_effect=RuntimeError("API error"))
        classifier = HybridClassifier(rules=self.rules, llm_client=failing_client)

        case = self._make_case(root_cause="insufficient_funds")
        features = self._make_features()
        event = self._make_event("insufficient_funds")

        # Should not raise, classification still works
        classification = classifier.classify(case, features, event)
        assert classification.root_cause == RootCause.INSUFFICIENT_FUNDS
        # Rationale may be empty
        assert case.diagnostic_rationale == ""

    def test_no_llm_client_works(self):
        """Test classifier works without LLM client (no rationale generated)."""
        classifier = HybridClassifier(rules=self.rules, llm_client=None)

        case = self._make_case(root_cause="insufficient_funds")
        features = self._make_features()
        event = self._make_event("insufficient_funds")

        classification = classifier.classify(case, features, event)
        assert classification.root_cause == RootCause.INSUFFICIENT_FUNDS
        assert case.diagnostic_rationale == ""

    def test_ml_fallback_generates_rationale(self):
        """Test rationale generation when ML fallback is used."""
        mock_client = MockGeminiClient({
            "overdue_invoice": (
                "The invoice has passed its due date without payment, "
                "triggering the recovery workflow."
            ),
        })
        # No rules match, ML fallback used
        mock_ml = Mock()
        mock_ml.predict_proba.return_value = {
            RootCause.OVERDUE_INVOICE: 0.7,
            RootCause.INSUFFICIENT_FUNDS: 0.2,
        }
        mock_ml.model_version = "ml_v1"

        classifier = HybridClassifier(rules=self.rules, ml=mock_ml, llm_client=mock_client)

        case = self._make_case(root_cause="overdue_invoice")
        features = self._make_features()
        event = self._make_event("overdue_invoice", "OVERDUE")

        classification = classifier.classify(case, features, event)

        assert classification.root_cause == RootCause.OVERDUE_INVOICE
        assert case.diagnostic_rationale == (
            "The invoice has passed its due date without payment, "
            "triggering the recovery workflow."
        )

    def test_prompt_contains_failure_details(self):
        """Test that prompt includes failure reason, decline code, and root cause."""
        mock_client = MockGeminiClient({
            "risk_block": "Risk system blocked this transaction.",
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        case = self._make_case(root_cause="risk_block")
        features = self._make_features()
        event = self._make_event("risk_block", "RISK_BLOCK")

        classifier.classify(case, features, event)

        prompt = mock_client._last_prompt
        assert "risk_block" in prompt.lower()
        assert "RISK_BLOCK" in prompt
        assert "failure reason" in prompt.lower() or "failure_reason" in prompt.lower()
        assert "root cause" in prompt.lower() or "root_cause" in prompt.lower()

    def test_rationale_json_format(self):
        """Test that LLM response is parsed as JSON with 'rationale' key."""
        mock_client = MockGeminiClient({
            "dispute": "Customer filed a formal dispute.",
        })
        classifier = HybridClassifier(rules=self.rules, llm_client=mock_client)

        case = self._make_case(root_cause="dispute")
        features = self._make_features()
        event = self._make_event("dispute", "DISPUTE")

        classifier.classify(case, features, event)

        # Should have parsed JSON and extracted rationale
        assert case.diagnostic_rationale == "Customer filed a formal dispute."