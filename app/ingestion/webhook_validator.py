"""HMAC-SHA256 webhook verification + secret rotation, replay-safe (§2.1)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_ALLOWED_SKEW_SECONDS = 300
DEFAULT_MAX_AGE_SECONDS = 900


class WebhookRejected(Exception):
    """Raised when a webhook fails verification or freshness checks.

    kind is a machine-readable error code:
      MISSING_SIGNATURE / MALFORMED_SIGNATURE / INVALID_SIGNATURE /
      REPLAYED / SKEWED / MALFORMED_PAYLOAD / MISSING_EVENT_ID
    """

    def __init__(self, reason: str, kind: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


def parse_signature(signature_header: str | None) -> str | None:
    """Extract the raw sha256 hex from `X-Razorpay-Signature`.

    Accepts both Razorpay's `sha256=<hex>` envelope and a bare 64-char hex.
    Rejects anything that does not parse as exactly one 64-char hex digest.
    """
    if not signature_header:
        return None
    sig = signature_header.strip()
    if sig.lower().startswith("sha256="):
        sig = sig[len("sha256="):].strip()
    if len(sig) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sig):
        return None
    return sig


def verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Constant-time HMAC-SHA256 verification. Never raise.

    Returns False on any malformed input, unknown envelope, or mismatch.
    """
    if not body or not secret:
        return False
    provided = parse_signature(signature_header)
    if provided is None:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided.lower())


def _created_at(payload: dict) -> int | None:
    """Read Razorpay's `created_at` epoch seconds (tolerates milliseconds)."""
    raw = payload.get("created_at")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value > 10**12:  # milliseconds (e.g. 1672531200000)
        value //= 1000
    return value


@dataclass
class Freshness:
    ok: bool
    reason: str = ""
    kind: str = ""
    created_at: int | None = None
    age_seconds: float | None = None


def check_freshness(
    payload: dict,
    *,
    now: float,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    allowed_skew_seconds: int = DEFAULT_ALLOWED_SKEW_SECONDS,
) -> Freshness:
    """Reject replayed (too old) and impossible (future) event timestamps."""
    created = _created_at(payload)
    if created is None:
        return Freshness(ok=True)  # no timestamp -> nothing to replay-check
    age = now - created
    if age > max_age_seconds:
        return Freshness(
            ok=False,
            reason=f"Event is {int(age)}s old (> {max_age_seconds}s); possible replay",
            kind="REPLAYED",
            created_at=created,
            age_seconds=age,
        )
    if age < -allowed_skew_seconds:
        return Freshness(
            ok=False,
            reason=f"Event timestamp {int(-age)}s in the future; clock skew",
            kind="SKEWED",
            created_at=created,
            age_seconds=age,
        )
    return Freshness(ok=True, created_at=created, age_seconds=age)


@dataclass
class VerifiedWebhook:
    """Outcome of a successful validation."""

    body: bytes
    payload: dict
    event_id: str
    event_type: str
    secret_used: str
    created_at: int | None = None
    age_seconds: float | None = None


def validate_event(
    body: bytes,
    signature_header: str | None,
    secrets: Iterable[str],
    *,
    now: float | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    allowed_skew_seconds: int = DEFAULT_ALLOWED_SKEW_SECONDS,
) -> VerifiedWebhook:
    """Full authenticator: signature + freshness + structure.

    `secrets` is ordered — current secret first, then previous secrets, which
    is what makes secret rotation seamless (see §13.6 platform awareness).
    Raises WebhookRejected on any failure; returns VerifiedWebhook on success.
    """
    resolved_now = now if now is not None else time.time()

    if not signature_header:
        raise WebhookRejected("Missing X-Razorpay-Signature header", "MISSING_SIGNATURE")
    if parse_signature(signature_header) is None:
        raise WebhookRejected("Malformed signature header", "MALFORMED_SIGNATURE")

    matched = [
        secret
        for secret in secrets
        if secret and verify_signature(body, signature_header, secret)
    ]
    if not matched:
        raise WebhookRejected(
            "HMAC-SHA256 verification failed against all configured secrets",
            "INVALID_SIGNATURE",
        )
    secret_used = matched[0]

    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise WebhookRejected(
            f"Payload is not valid JSON: {exc}", "MALFORMED_PAYLOAD"
        ) from exc

    freshness = check_freshness(
        payload,
        now=resolved_now,
        max_age_seconds=max_age_seconds,
        allowed_skew_seconds=allowed_skew_seconds,
    )
    if not freshness.ok:
        raise WebhookRejected(freshness.reason, freshness.kind)

    event_id = payload.get("id")
    if not isinstance(event_id, str) or not event_id:
        raise WebhookRejected("Payload has no event id", "MISSING_EVENT_ID")

    return VerifiedWebhook(
        body=body,
        payload=payload,
        event_id=event_id,
        event_type=str(payload.get("entity") or payload.get("event") or "unknown"),
        secret_used=secret_used,
        created_at=freshness.created_at,
        age_seconds=freshness.age_seconds,
    )