"""Event Gateway — receives & validates webhooks, dedups, routes to DLQ."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.config import get_settings
from app.ingestion.dead_letter_queue import DeadLetterQueue
from app.ingestion.event_inbox import DedupDecision, EventInbox
from app.ingestion.webhook_validator import (
    WebhookRejected,
    validate_event,
)

SIGNATURE_HEADER = "x-razorpay-signature"


class WebhookResultStatus:
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


@dataclass
class WebhookResult:
    status: str
    event_id: str | None = None
    reason: str | None = None
    kind: str | None = None
    dlq_message_id: str | None = None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


class WebhookHandler:
    """HMAC-verify → freshness check → dedup → hand off, else DLQ."""

    def __init__(
        self,
        inbox: EventInbox | None = None,
        dlq: DeadLetterQueue | None = None,
        secrets: Iterable[str] | None = None,
        *,
        max_age_seconds: int = 900,
        allowed_skew_seconds: int = 300,
    ) -> None:
        self.inbox = inbox or EventInbox()
        self.dlq = dlq or DeadLetterQueue()
        self._secrets: list[str] | None = list(secrets) if secrets is not None else None
        self.max_age_seconds = max_age_seconds
        self.allowed_skew_seconds = allowed_skew_seconds

    def _resolve_secrets(self) -> list[str]:
        if self._secrets is not None:
            return self._secrets
        settings = get_settings()
        secrets = [settings.WEBHOOK_SECRET]
        if settings.WEBHOOK_SECRET_PREVIOUS:
            secrets.append(settings.WEBHOOK_SECRET_PREVIOUS)
        return secrets

    def handle(self, body: bytes, headers: Mapping[str, str]) -> WebhookResult:
        """Process one incoming webhook. Never raises for bad input."""
        signature = _header(headers, SIGNATURE_HEADER)
        try:
            verified = validate_event(
                body,
                signature,
                self._resolve_secrets(),
                max_age_seconds=self.max_age_seconds,
                allowed_skew_seconds=self.allowed_skew_seconds,
            )
        except WebhookRejected as exc:
            dlq_id = self._dlq_entry(
                body, headers, event_id=_best_effort_event_id(body), exc=exc
            )
            return WebhookResult(
                status=WebhookResultStatus.REJECTED,
                kind=exc.kind,
                reason=exc.reason,
                dlq_message_id=dlq_id,
            )
        except Exception as exc:  # noqa: BLE001 - gateway boundary safety net
            dlq_id = self._dlq_entry(
                body, headers, event_id=_best_effort_event_id(body), exc=exc
            )
            return WebhookResult(
                status=WebhookResultStatus.ERROR,
                kind="INTERNAL_ERROR",
                reason=str(exc),
                dlq_message_id=dlq_id,
            )

        decision = self.inbox.insert(verified.event_id)
        if decision is DedupDecision.DUPLICATE:
            return WebhookResult(
                status=WebhookResultStatus.DUPLICATE, event_id=verified.event_id
            )
        return WebhookResult(
            status=WebhookResultStatus.ACCEPTED, event_id=verified.event_id
        )

    def _dlq_entry(
        self,
        body: bytes,
        headers: Mapping[str, str],
        *,
        event_id: str | None,
        exc: Exception,
    ) -> str:
        return self.dlq.push(
            kind=getattr(exc, "kind", "INTERNAL_ERROR"),
            reason=str(exc),
            event_id=event_id,
            payload=_best_effort_json(body),
            headers=dict(headers),
        )


def _best_effort_event_id(body: bytes) -> str | None:
    payload = _best_effort_json(body)
    if isinstance(payload, dict):
        event_id = payload.get("id")
        return event_id if isinstance(event_id, str) else None
    return None


def _best_effort_json(body: bytes):
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None
