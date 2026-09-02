"""Notification sender — SMS/Email/WhatsApp (mocked) via template engine (§10.7).

Financial amounts are backend truth via the template engine — the LLM is
never allowed to invent them (§10.7).
"""

from __future__ import annotations

import dataclasses
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
    state: ExecutionState


class NotificationSender:
    """Sends templated SMS/Email/WhatsApp messages (mocked transport)."""

    def __init__(self) -> None:
        self._sent: list[NotificationRecord] = []

        # Extremely basic mock template store
        self._templates: dict[str, str] = {
            "payment_failed": "Your payment of ₹{amount} failed. Please pay here: {link}",
            "ptp_reminder": "Reminder: You promised to pay ₹{amount} on {date}. Link: {link}",
            "partial_offer": "We can accept a partial payment of ₹{amount}. Link: {link}",
        }

    def send(
        self, channel: str, template_key: str, variables: dict, note: str = ""
    ) -> NotificationRecord:
        """Render a template securely with backend variables and mock-send it."""
        template_str = self._templates.get(
            template_key, "Message regarding your account. Variables: {variables}"
        )

        # Render securely — LLM cannot inject into this path directly
        try:
            rendered = template_str.format(**variables, variables=variables)
        except KeyError as e:
            logger.error("Missing template variable: %s", e)
            rendered = f"Error rendering template {template_key}"

        logger.info(
            "Mock sending %s via %s. Content: %s", template_key, channel, rendered
        )

        record = NotificationRecord(
            notification_id=f"notif_{uuid.uuid4().hex[:12]}",
            channel=channel,
            template_key=template_key,
            rendered_body=rendered,
            state=ExecutionState.SUCCESS,  # Mocked success
        )

        self._sent.append(record)
        return record
