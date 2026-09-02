"""Notification sender — SMS/Email/WhatsApp (mocked) via template engine (§10.7).

Financial amounts are backend truth via the template engine — the LLM is
never allowed to invent them (§10.7).

This module logs realistic payloads but does NOT actually send anything.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid

from app.contracts import ExecutionState

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class NotificationRecord:
    """A rendered, sent (mocked) notification."""

    notification_id: str
    channel: str
    template_key: str
    rendered_body: str
    recipient: str
    state: ExecutionState


# Realistic template library — the LLM cannot touch these strings.
# It can only choose a template_key and supply backend-verified variables.
_TEMPLATES: dict[str, dict] = {
    "payment_failed_sms": {
        "body": (
            "[{merchant_name}] Your payment of ₹{amount} for order "
            "{order_id} could not be processed. Complete payment: {link}"
        ),
        "required": ["merchant_name", "amount", "order_id", "link"],
    },
    "payment_failed_email": {
        "body": (
            "Dear {customer_name},\n\n"
            "Your payment of ₹{amount} for order {order_id} with "
            "{merchant_name} could not be processed.\n\n"
            "Reason: {failure_reason}\n\n"
            "You can complete your payment using this secure link:\n"
            "{link}\n\n"
            "This link expires on {expiry_date}.\n\n"
            "If you have already paid, please disregard this message.\n\n"
            "— {merchant_name}"
        ),
        "required": [
            "customer_name",
            "amount",
            "order_id",
            "merchant_name",
            "failure_reason",
            "link",
            "expiry_date",
        ],
    },
    "ptp_reminder": {
        "body": (
            "[{merchant_name}] Reminder: You agreed to pay ₹{amount} on "
            "{ptp_date}. Pay here: {link}"
        ),
        "required": ["merchant_name", "amount", "ptp_date", "link"],
    },
    "subscription_dunning": {
        "body": (
            "[{merchant_name}] Your subscription (₹{amount}/{cycle}) "
            "payment failed. Update payment method: {link}"
        ),
        "required": ["merchant_name", "amount", "cycle", "link"],
    },
    "partial_payment_offer": {
        "body": (
            "[{merchant_name}] We can accept a partial payment of "
            "₹{partial_amount} (of ₹{total_amount}). Pay here: {link}"
        ),
        "required": ["merchant_name", "partial_amount", "total_amount", "link"],
    },
    "payment_method_update": {
        "body": (
            "[{merchant_name}] Your card ending {last4} has expired. "
            "Update your payment method: {link}"
        ),
        "required": ["merchant_name", "last4", "link"],
    },
}


class NotificationSender:
    """Sends templated SMS/Email/WhatsApp messages (mocked transport).

    Logs realistic payloads for demo purposes but does NOT actually send.
    Financial amounts are backend truth via the template engine — the LLM is
    never allowed to invent them (§10.7).
    """

    def __init__(self) -> None:
        self._sent: list[NotificationRecord] = []

    def send(
        self,
        channel: str,
        template_key: str,
        variables: dict,
        recipient: str = "",
        note: str = "",
    ) -> NotificationRecord:
        """Render a template securely with backend variables and mock-send it.

        The LLM provides template_key; the backend injects verified amounts.
        """
        template = _TEMPLATES.get(template_key)

        if template is None:
            # Fall back to a generic safe template
            rendered = (
                f"[Recovery Notification] template={template_key} "
                f"vars={json.dumps(variables, default=str)}"
            )
            logger.warning("Unknown template_key '%s' — using fallback", template_key)
        else:
            # Validate required variables
            missing = [k for k in template["required"] if k not in variables]
            if missing:
                logger.error(
                    "Missing template variables for '%s': %s",
                    template_key,
                    missing,
                )
                rendered = (
                    f"[RENDER ERROR] template={template_key} " f"missing={missing}"
                )
            else:
                try:
                    rendered = template["body"].format(**variables)
                except (KeyError, ValueError) as e:
                    logger.error("Template render error for '%s': %s", template_key, e)
                    rendered = f"[RENDER ERROR] template={template_key} error={e}"

        # Log the realistic payload — this IS the mock "send"
        logger.info(
            "═══ MOCK %s NOTIFICATION ═══\n"
            "  Channel:    %s\n"
            "  Template:   %s\n"
            "  Recipient:  %s\n"
            "  Content:    %s\n"
            "  Note:       %s\n"
            "═══════════════════════════",
            channel.upper(),
            channel,
            template_key,
            recipient or "(no recipient)",
            rendered,
            note or "(none)",
        )

        record = NotificationRecord(
            notification_id=f"notif_{uuid.uuid4().hex[:12]}",
            channel=channel,
            template_key=template_key,
            rendered_body=rendered,
            recipient=recipient,
            state=ExecutionState.SUCCESS,  # Mocked — always succeeds
        )

        self._sent.append(record)
        return record

    @property
    def sent_count(self) -> int:
        return len(self._sent)

    @property
    def sent_log(self) -> list[NotificationRecord]:
        return list(self._sent)
