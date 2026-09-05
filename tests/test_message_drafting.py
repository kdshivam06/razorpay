"""Test suite: Channel Message Drafting (§10.7, Track E step 6).

Verifies that SMS / WhatsApp / Email / Voice drafts are generated per
register (Standard English and Hinglish), that every amount and date in the
output matches the case record exactly (§2.3 LLM Role Boundaries), and that
LLM hallucinations (amounts/dates invented outside the case record) are
rejected and regenerated before anything is sendable.
"""

from datetime import datetime, timezone
from typing import Any

from app.nlp.gemini_client import JsonLlmClient
from app.nlp.message_templates import (
    REGISTERS,
    ChannelDraftGenerator,
    build_draft_context,
    extract_amounts,
    extract_dates,
    validate_draft,
)


class MockGeminiClient(JsonLlmClient):
    """Serves scripted JSON payloads in order; records every prompt."""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate_json(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        if self._responses:
            return self._responses.pop(0)
        return {}


class FailingGeminiClient(JsonLlmClient):
    def generate_json(self, prompt: str) -> dict[str, Any]:
        raise RuntimeError("model unavailable")


def _make_context(amount_paise: int = 15000000) -> Any:
    return build_draft_context(
        case_id="case_test_01",
        amount_paise=amount_paise,
        trigger_dt=datetime(2026, 9, 5, tzinfo=timezone.utc),
        root_cause="insufficient_funds",
    )


def _valid_sms(ctx) -> str:
    v = ctx.variables
    return f"Pay {v['amount']} using this link {v['payment_link']} before {v['expiry']}."


def _valid_email(ctx) -> tuple[str, str]:
    v = ctx.variables
    subject = f"Payment of {v['amount']} pending"
    body = (
        f"Hi {v['customer_name']}, pay {v['amount']} here {v['payment_link']} "
        f"before {v['expiry']}."
    )
    return subject, body


class TestFallbackDrafts:
    """Deterministic template rendering when no LLM is available (§2.4)."""

    def setup_method(self):
        self.ctx = _make_context()
        self.generator = ChannelDraftGenerator()

    def test_all_channels_and_registers_render_valid_drafts(self):
        drafts = self.generator.generate(self.ctx)
        for channel in ("sms", "whatsapp", "email", "voice_script"):
            for register in REGISTERS:
                draft = drafts[channel][register]
                assert draft.source == "fallback"
                assert draft.body.strip()
                assert validate_draft(self.ctx, draft.subject, draft.body) == []

    def test_amount_is_exactly_the_case_amount(self):
        drafts = self.generator.generate(self.ctx)
        for channel in ("sms", "whatsapp", "email", "voice_script"):
            for register in REGISTERS:
                draft = drafts[channel][register]
                assert extract_amounts(draft.body) == [self.ctx.amount_paise]

    def test_every_date_is_within_allowed_dates(self):
        drafts = self.generator.generate(self.ctx)
        for channel in ("sms", "whatsapp", "email", "voice_script"):
            for register in REGISTERS:
                draft = drafts[channel][register]
                text = f"{draft.subject}\n{draft.body}"
                for iso in extract_dates(text):
                    assert iso in self.ctx.allowed_dates

    def test_fallback_sms_respects_unicode_length_limit(self):
        drafts = self.generator.generate(self.ctx)
        body = drafts["sms"]["en"].body
        assert len(body) <= 70  # ₹ requires a UCS-2 SMS segment

    def test_registers_produce_distinct_text(self):
        drafts = self.generator.generate(self.ctx)
        assert drafts["sms"]["en"].body != drafts["sms"]["hi-en"].body
        assert drafts["voice_script"]["en"].body != drafts["voice_script"]["hi-en"].body

    def test_email_carries_subject_and_body(self):
        drafts = self.generator.generate(self.ctx)
        for register in REGISTERS:
            email = drafts["email"][register]
            assert email.subject
            assert email.body
            assert "https://" in email.body


class TestLlmGenerationWithGuardrails:
    """LLM drafts are re-validated; hallucinated values are rejected (§2.3)."""

    def setup_method(self):
        self.ctx = _make_context()

    def test_hallucinated_amount_rejected_then_regenerated(self):
        # First response invents a different amount (₹9,99,999).
        bad = {
            "body": f"Pay INR 9,99,999 using this link "
                    f"{self.ctx.variables['payment_link']} before "
                    f"{self.ctx.variables['expiry']}."
        }
        good = {"body": _valid_sms(self.ctx)}
        mock = MockGeminiClient([bad, good])
        generator = ChannelDraftGenerator(llm_client=mock, sms_unicode_limit=160)

        draft = generator.generate_channel(self.ctx, "sms")["en"]

        assert draft.source == "llm"
        assert draft.attempts == 2  # bad attempt consumed, good attempt succeeded
        assert "9,99,999" not in draft.body
        assert extract_amounts(draft.body) == [self.ctx.amount_paise]
        assert validate_draft(self.ctx, draft.subject, draft.body) == []

    def test_hallucinated_date_rejected_then_regenerated(self):
        bad = {
            "body": f"Pay {self.ctx.variables['amount']} by 31 Dec 2030 "
                    f"using {self.ctx.variables['payment_link']}."
        }
        good = {"body": _valid_sms(self.ctx)}
        mock = MockGeminiClient([bad, good])
        generator = ChannelDraftGenerator(llm_client=mock, sms_unicode_limit=160)

        draft = generator.generate_channel(self.ctx, "sms")["en"]

        assert draft.source == "llm"
        assert draft.attempts == 2
        assert "2030" not in draft.body
        for iso in extract_dates(draft.body):
            assert iso in self.ctx.allowed_dates

    def test_two_llm_failures_fall_back_to_templates(self):
        bad = {
            "body": f"Pay INR 9,99,999 here {self.ctx.variables['payment_link']}. "
                    f"Due 31 Dec 2030."
        }
        mock = MockGeminiClient([bad, bad])
        generator = ChannelDraftGenerator(llm_client=mock, sms_unicode_limit=160)

        draft = generator.generate_channel(self.ctx, "sms")["en"]

        assert draft.source == "fallback"
        assert draft.attempts == 2
        assert validate_draft(self.ctx, draft.subject, draft.body) == []

    def test_empty_response_falls_back(self):
        generator = ChannelDraftGenerator(llm_client=MockGeminiClient([{}]))
        draft = generator.generate_channel(self.ctx, "sms")["en"]
        assert draft.source == "fallback"
        assert validate_draft(self.ctx, draft.subject, draft.body) == []

    def test_runtime_error_falls_back(self):
        generator = ChannelDraftGenerator(llm_client=FailingGeminiClient())
        draft = generator.generate_channel(self.ctx, "sms")["en"]
        assert draft.source == "fallback"

    def test_email_subject_and_body_used_from_llm(self):
        subject, body = _valid_email(self.ctx)
        mock = MockGeminiClient([{"subject": subject, "body": body}])
        generator = ChannelDraftGenerator(llm_client=mock)

        draft = generator.generate_channel(self.ctx, "email")["en"]

        assert draft.source == "llm"
        assert draft.attempts == 1
        assert draft.subject == subject
        assert extract_amounts(draft.body) == [self.ctx.amount_paise]

    def test_voice_script_extracts_from_script_field(self):
        v = self.ctx.variables
        script = (
            f"Hello {v['customer_name']}, please pay {v['amount']} using the "
            f"link sent to you before {v['expiry']}."
        )
        mock = MockGeminiClient([{"script": script}])
        generator = ChannelDraftGenerator(llm_client=mock)

        draft = generator.generate_channel(self.ctx, "voice_script")["en"]

        assert draft.source == "llm"
        assert "script" not in draft.body
        assert extract_amounts(draft.body) == [self.ctx.amount_paise]

    def test_sms_over_unicode_limit_rejected_then_regenerated(self):
        bad = {"body": "₹" + "a" * 165}
        good = {"body": f"Pay {self.ctx.variables['amount']} now."}
        mock = MockGeminiClient([bad, good])
        generator = ChannelDraftGenerator(llm_client=mock)

        draft = generator.generate_channel(self.ctx, "sms")["en"]

        assert draft.source == "llm"
        assert draft.attempts == 2
        assert len(draft.body) <= 70


class TestExtractors:
    """Unit coverage for the hallucination-guard extractors."""

    def test_extract_amount_inr_rupee_rs_formats(self):
        assert extract_amounts("Pay ₹1,50,000 and INR 20000 and Rs. 3,999.50") == [
            15000000,
            2000000,
            399950,
        ]

    def test_extract_iso_and_verbal_dates(self):
        assert extract_dates("due 2026-09-12 or 31 Dec 2030") == [
            "2026-09-12",
            "2030-12-31",
        ]

    def test_paise_zero_matches(self):
        ctx = _make_context(amount_paise=1)
        assert extract_amounts("Nothing owed ₹0.01") == [1]
        assert validate_draft(ctx, "", "Nothing owed ₹0.01") == []