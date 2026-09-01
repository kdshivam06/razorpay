"""Recovery message template engine (§10.7)."""

from __future__ import annotations

import dataclasses
import string

from app.nlp.gemini_client import normalize


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

    def __init__(self, templates: dict[str, str] | None = None) -> None:
        self._templates = dict(DEFAULT_TEMPLATES)
        if templates:
            self._templates.update(
                {key.upper(): value for key, value in templates.items()}
            )

    def render(
        self, template_key: str, variables: dict, locale: str = "en"
    ) -> RenderedTemplate:
        key = _resolve_key(template_key, locale, self._templates)
        if key not in self._templates:
            raise KeyError(f"Unknown template: {template_key}")
        template = self._templates[key]
        required = tuple(
            field_name
            for _, field_name, _, _ in string.Formatter().parse(template)
            if field_name
        )
        missing = [name for name in required if name not in variables]
        if missing:
            raise ValueError(
                f"Missing template variables: {', '.join(sorted(missing))}"
            )
        unsafe = [name for name in variables if str(name).startswith("true_")]
        if unsafe:
            raise ValueError("Evaluation labels cannot be used in customer templates")

        safe_variables = {
            name: _render_value(name, variables[name]) for name in required
        }
        body = template.format(**safe_variables)
        if _looks_like_unresolved_variable(body):
            raise ValueError("Template rendered with unresolved variables")
        return RenderedTemplate(
            template_key=key,
            body=body,
            used_variables=required,
        )

    def has_template(self, template_key: str) -> bool:
        key = template_key.upper()
        return key in self._templates or any(
            existing.startswith(f"{key}:") for existing in self._templates
        )


DEFAULT_TEMPLATES = {
    "INSUFFICIENT_FUNDS:en": (
        "Hi {customer_name}, your payment of {amount} to {merchant_name} did not "
        "go through due to insufficient funds. You can complete it here: "
        "{payment_link}. Link expires on {expiry}."
    ),
    "INSUFFICIENT_FUNDS:hinglish": (
        "Hi {customer_name}, {merchant_name} ke liye {amount} payment fail ho gaya. "
        "Pay karne ke liye link use karein: {payment_link}. Expiry: {expiry}."
    ),
    "EXPIRED_CARD:en": (
        "Hi {customer_name}, your saved card could not be charged for {amount}. "
        "Please update your payment method here: {payment_link}."
    ),
    "OVERDUE_INVOICE:en": (
        "Hi {customer_name}, invoice {invoice_id} for {amount} from {merchant_name} "
        "is overdue. Please pay by {due_date}: {payment_link}."
    ),
    "PTP_CONFIRMATION:en": (
        "Thanks {customer_name}. We have noted your promise to pay {amount} by "
        "{promised_date}."
    ),
    "PAYMENT_LINK:en": (
        "Hi {customer_name}, here is your secure payment link for {amount}: "
        "{payment_link}. It expires on {expiry}."
    ),
}


def _resolve_key(template_key: str, locale: str, templates: dict[str, str]) -> str:
    key = template_key.upper()
    localized = f"{key}:{locale.lower()}"
    if localized in templates:
        return localized
    return f"{key}:en" if f"{key}:en" in templates else key


def _render_value(name: str, value: object) -> str:
    text = str(value)
    if (
        name in {"amount", "payment_link", "expiry", "due_date", "promised_date"}
        and not text.strip()
    ):
        raise ValueError(f"Template variable {name!r} cannot be blank")
    if any(
        token in normalize(text)
        for token in ("ignore previous", "system override", "reveal hidden")
    ):
        raise ValueError(f"Template variable {name!r} contains unsafe instruction text")
    return text


def _looks_like_unresolved_variable(body: str) -> bool:
    return "{" in body or "}" in body
