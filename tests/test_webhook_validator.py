"""Tests for the webhook validator — signature, replay, secret rotation."""

import hashlib
import hmac
import json

import pytest

from app.ingestion.webhook_validator import (
    WebhookRejected,
    parse_signature,
    validate_event,
    verify_signature,
)

NOW = 1_700_000_000
CURRENT_SECRET = "whsec_current_0123456789"
PREVIOUS_SECRET = "whsec_previous_0123456789"


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def envelope(body: bytes, secret: str) -> str:
    return f"sha256={sign(body, secret)}"


def webhook_body(
    event_id: str = "evt_1001",
    created_at: int = NOW,
) -> bytes:
    return json.dumps(
        {"id": event_id, "entity": "event", "entity.event": "payment.failed", "created_at": created_at}
    ).encode()


@pytest.fixture(autouse=True)
def _fresh_time(monkeypatch):
    monkeypatch.setattr(
        "app.ingestion.webhook_validator.time.time", lambda: NOW
    )


class TestSignatureParsing:
    def test_bare_hex_accepted(self):
        signature = "a" * 64
        assert parse_signature(signature) == signature

    def test_sha256_envelope_accepted(self):
        raw = "a" * 64
        assert parse_signature(f"sha256={raw}") == raw
        assert parse_signature(f"SHA256={raw}") == raw

    def test_malformed_rejected(self):
        for bad in ("", "   ", "abc", "a" * 63, "a" * 65, "sha256=zz", "t=1,v1=abc"):
            assert parse_signature(bad) is None

    def test_whitespace_tolerated(self):
        raw = "a" * 64
        assert parse_signature(f"  sha256={raw}  ") == raw


class TestVerifySignature:
    def test_valid_bare_signature(self):
        body = webhook_body()
        assert verify_signature(body, sign(body, CURRENT_SECRET), CURRENT_SECRET) is True

    def test_valid_sha256_envelope(self):
        body = webhook_body()
        assert (
            verify_signature(body, envelope(body, CURRENT_SECRET), CURRENT_SECRET)
            is True
        )

    def test_wrong_secret_rejected(self):
        body = webhook_body()
        assert (
            verify_signature(body, sign(body, "wrong_secret"), CURRENT_SECRET)
            is False
        )

    def test_not_constant_but_safe_empty_inputs(self):
        assert verify_signature(b"", None, CURRENT_SECRET) is False
        assert verify_signature(webhook_body(), "a" * 64, "") is False


class TestValidateEvent:
    def test_valid_signature_accepted(self):
        body = webhook_body()
        verified = validate_event(
            body, envelope(body, CURRENT_SECRET), [CURRENT_SECRET], now=NOW
        )
        assert verified.event_id == "evt_1001"
        assert verified.secret_used == CURRENT_SECRET
        assert verified.created_at == NOW

    def test_invalid_signature_rejected(self):
        body = webhook_body()
        bad = sign(body, "attacker_secret")
        with pytest.raises(WebhookRejected) as exc:
            validate_event(body, bad, [CURRENT_SECRET], now=NOW)
        assert exc.value.kind == "INVALID_SIGNATURE"
        assert "HMAC" in exc.value.reason

    def test_missing_signature_rejected(self):
        with pytest.raises(WebhookRejected) as exc:
            validate_event(webhook_body(), None, [CURRENT_SECRET], now=NOW)
        assert exc.value.kind == "MISSING_SIGNATURE"

    def test_malformed_signature_rejected(self):
        with pytest.raises(WebhookRejected) as exc:
            validate_event(webhook_body(), "garbage-signature", [CURRENT_SECRET], now=NOW)
        assert exc.value.kind == "MALFORMED_SIGNATURE"

    def test_tampered_body_rejected(self):
        body = webhook_body(created_at=NOW)
        signature = envelope(body, CURRENT_SECRET)
        tampered = bytearray(body)
        tampered[10] = ord("x") if tampered[10] != ord("x") else ord("y")
        with pytest.raises(WebhookRejected) as exc:
            validate_event(bytes(tampered), signature, [CURRENT_SECRET], now=NOW)
        assert exc.value.kind == "INVALID_SIGNATURE"

    def test_missing_event_id_rejected(self):
        body = json.dumps({"entity": "event", "created_at": NOW}).encode()
        with pytest.raises(WebhookRejected) as exc:
            validate_event(body, envelope(body, CURRENT_SECRET), [CURRENT_SECRET], now=NOW)
        assert exc.value.kind == "MISSING_EVENT_ID"


class TestReplayProtection:
    def test_replayed_old_timestamp_rejected(self):
        body = webhook_body(created_at=NOW - 100_000)
        with pytest.raises(WebhookRejected) as exc:
            validate_event(
                body,
                envelope(body, CURRENT_SECRET),
                [CURRENT_SECRET],
                now=NOW,
                max_age_seconds=900,
            )
        assert exc.value.kind == "REPLAYED"
        assert "replay" in exc.value.reason

    def test_fresh_timestamp_accepted_at_boundary(self):
        body = webhook_body(created_at=NOW - 900)
        verified = validate_event(
            body,
            envelope(body, CURRENT_SECRET),
            [CURRENT_SECRET],
            now=NOW,
            max_age_seconds=900,
        )
        assert verified.event_id == "evt_1001"

    def test_future_timestamp_rejected_as_skewed(self):
        body = webhook_body(created_at=NOW + 500)
        with pytest.raises(WebhookRejected) as exc:
            validate_event(
                body,
                envelope(body, CURRENT_SECRET),
                [CURRENT_SECRET],
                now=NOW,
                allowed_skew_seconds=300,
            )
        assert exc.value.kind == "SKEWED"

    def test_millisecond_timestamp_normalized(self):
        body = webhook_body(created_at=NOW * 1000)
        verified = validate_event(
            body, envelope(body, CURRENT_SECRET), [CURRENT_SECRET], now=NOW
        )
        assert verified.created_at == NOW

    def test_missing_timestamp_accepted(self):
        body = json.dumps({"id": "evt_no_ts", "entity": "event"}).encode()
        verified = validate_event(
            body, envelope(body, CURRENT_SECRET), [CURRENT_SECRET], now=NOW
        )
        assert verified.event_id == "evt_no_ts"


class TestSecretRotation:
    def test_current_secret_works(self):
        body = webhook_body()
        verified = validate_event(
            body, envelope(body, CURRENT_SECRET), [CURRENT_SECRET, PREVIOUS_SECRET], now=NOW
        )
        assert verified.secret_used == CURRENT_SECRET

    def test_previous_secret_works_during_rotation_window(self):
        body = webhook_body()
        verified = validate_event(
            body,
            envelope(body, PREVIOUS_SECRET),
            [CURRENT_SECRET, PREVIOUS_SECRET],
            now=NOW,
        )
        assert verified.secret_used == PREVIOUS_SECRET

    def test_unknown_secret_rejected_under_rotation(self):
        body = webhook_body()
        with pytest.raises(WebhookRejected) as exc:
            validate_event(
                body,
                sign(body, "something_else"),
                [CURRENT_SECRET, PREVIOUS_SECRET],
                now=NOW,
            )
        assert exc.value.kind == "INVALID_SIGNATURE"

    def test_current_secret_alone_rejects_previous(self):
        body = webhook_body()
        with pytest.raises(WebhookRejected) as exc:
            validate_event(body, envelope(body, PREVIOUS_SECRET), [CURRENT_SECRET], now=NOW)
        assert exc.value.kind == "INVALID_SIGNATURE"


class TestReplayWithOldSecret:
    def test_replay_signed_with_previous_secret_still_rejected(self):
        body = webhook_body(created_at=NOW - 100_000)
        with pytest.raises(WebhookRejected) as exc:
            validate_event(
                body,
                envelope(body, PREVIOUS_SECRET),
                [CURRENT_SECRET, PREVIOUS_SECRET],
                now=NOW,
                max_age_seconds=900,
            )
        assert exc.value.kind == "REPLAYED"  # freshness check runs before trust