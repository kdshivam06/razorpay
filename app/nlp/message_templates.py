"""Recovery message template engine (§10.7)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class RenderedTemplate:
    """A fully-rendered recovery message with guardrails applied."""

    template_key: str
    body: str
    used_variables: tuple[str, ...]


class TemplateEngine:
    """Renders recovery messages from plain templates.

    The LLM is NEVER allowed to invent financial amounts — variables such as
    customer_name, amount, payment_link, expiry, merchant_name ALL come from
    backend truth (§10.7)."""

    def render(self, template_key: str, variables: dict, locale: str = "en") -> RenderedTemplate:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §10.7")

    def has_template(self, template_key: str) -> bool:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §10.7")