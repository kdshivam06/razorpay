"""Notification sender — SMS/Email/WhatsApp (mocked) via template engine (§10.7)."""

from __future__ import annotations

import dataclasses

from app.contracts import ExecutionState


@dataclasses.dataclass(frozen=True)
class NotificationRecord:
    """A rendered, sent (mocked) notification."""

    notification_id: str
    channel: str
    template_key: str
    rendered_body: str
    state: ExecutionState


class NotificationSender:
    """Sends templated SMS/Email/WhatsApp messages (mocked transport).

    Financial amounts are backend truth via the template engine — the LLM is
    never allowed to invent them (§10.7)."""

    def send(self, channel: str, template_key: str, variables: dict, note: str = "") -> NotificationRecord:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §10.7")