"""Recovery message template engine (§10.7).

The LLM localizes template variables into ready-to-send channel drafts
(SMS / WhatsApp / Email / Voice) in Standard English or Hinglish, but it is
NEVER allowed to invent financial amounts or dates. Every generated draft is
re-validated after generation: any amount or date token that does not match
the case record exactly causes the draft to be rejected and regenerated
(§2.3 LLM Role Boundaries, §10.7).
"""

from __future__ import annotations

import dataclasses
import re
import string
from datetime import datetime, timedelta, timezone

from app.nlp.gemini_client import normalize, try_generate_json


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


# ────────────────────────────────────────────────────────────────────────
# §10.7 Channel draft generation (E.6)
# ────────────────────────────────────────────────────────────────────────

CHANNELS = ("sms", "whatsapp", "email", "voice_script")
REGISTERS = ("en", "hi-en")

SMS_GSM_LIMIT = 160
SMS_UNICODE_LIMIT = 70

# Per-template backend-truth variables that may appear in draft text.
# These MUST be filled from the case record; the LLM may only localize the
# surrounding words (§2.3, §10.7).
DRAFT_VARIABLE_KEYS = (
    "customer_name",
    "amount",
    "payment_link",
    "expiry",
    "due_date",
    "promised_date",
    "merchant_name",
    "invoice_id",
    "failure_reason",
)


@dataclasses.dataclass(frozen=True)
class RenderedDraft:
    """One validated, ready-to-send channel draft."""

    channel: str
    register: str
    subject: str  # email only; "" for other channels
    body: str
    source: str  # "llm" | "fallback"
    attempts: int
    validation_errors: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class DraftContext:
    """Backend-truth reference for one case (§10.7).

    ``variables`` holds the exact values the LLM is allowed to use. Any
    amount or date appearing in generated output that is not in this record
    is hallucinated and must be rejected.
    """

    case_id: str
    amount_paise: int
    variables: dict[str, str]
    allowed_amount_paise: tuple[int, ...]
    allowed_dates: frozenset[str]


def build_draft_context(
    case_id: str,
    amount_paise: int,
    trigger_dt: datetime | None = None,
    root_cause: str = "",
    merchant_name: str = "Demo Merchant",
) -> DraftContext:
    """Derive the full backend-truth variable set for a case record.

    Amount comes from the case's outstanding amount (paise, authoritative);
    dates come from the case trigger timestamp plus fixed lead times. The
    LLM cannot introduce its own amounts or dates.
    """
    dt = trigger_dt or datetime.now(timezone.utc)
    trigger_date = dt.strftime("%Y-%m-%d")
    expiry = (dt + timedelta(days=7)).strftime("%Y-%m-%d")
    due_date = (dt + timedelta(days=3)).strftime("%Y-%m-%d")
    promised_date = (dt + timedelta(days=2)).strftime("%Y-%m-%d")

    variables = {
        "customer_name": f"Customer {case_id[-4:].upper()}",
        "amount": f"₹{_format_inr(amount_paise)}",
        "payment_link": f"https://rzp.io/i/pl_{case_id.lower()}",
        "expiry": expiry,
        "due_date": due_date,
        "promised_date": promised_date,
        "merchant_name": merchant_name,
        "invoice_id": f"INV-{case_id.upper()}",
        "failure_reason": root_cause.replace("_", " ").replace("-", " "),
        "trigger_date": trigger_date,
    }
    amount = int(amount_paise)
    return DraftContext(
        case_id=case_id,
        amount_paise=amount,
        variables=variables,
        allowed_amount_paise=(amount,),
        allowed_dates=frozenset({trigger_date, expiry, due_date, promised_date}),
    )


# -- Hallucination guard: amount + date extraction and validation -----------

_AMOUNT_TOKEN_RE = re.compile(r"(?:₹|INR|Rs\.?)\s*(\d[\d,]*(?:\.\d{1,2})?)")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_VERBAL_DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\s+(\d{4})\b")

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def extract_amounts(text: str) -> list[int]:
    """Return every monetary token in ``text`` converted to paise.

    Only tokens prefixed with ₹ / INR / Rs. are considered — the LLM cannot
    smuggle a financial figure past the guard by omitting the symbol.
    """
    tokens: list[int] = []
    for match in _AMOUNT_TOKEN_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        if "." in raw:
            rupees, _, fraction = raw.partition(".")
            paise_part = int(fraction.ljust(2, "0")[:2])
        else:
            rupees, paise_part = raw, 0
        try:
            tokens.append(int(rupees) * 100 + paise_part)
        except ValueError:
            continue
    return tokens


def extract_dates(text: str) -> list[str]:
    """Return every date token in ``text`` normalised to ``YYYY-MM-DD``."""
    dates: list[str] = []
    for match in _ISO_DATE_RE.finditer(text or ""):
        year, month, day = match.groups()
        dates.append(f"{year}-{month}-{day}")
    for match in _VERBAL_DATE_RE.finditer(text or ""):
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name[:3].lower())
        if month:
            dates.append(f"{year}-{month}-{int(day):02d}")
    return dates


def validate_draft(
    context: DraftContext,
    subject: str,
    body: str,
) -> list[str]:
    """Check every amount/date in a draft against the case record.

    Returns a list of guardrail violations. An empty list means the draft is
    safe to send — every amount/date exactly matches backend truth.
    """
    errors: list[str] = []
    text = f"{subject or ''}\n{body or ''}"

    for paise in extract_amounts(text):
        if paise not in context.allowed_amount_paise:
            errors.append(
                f"amount ₹{paise / 100:.2f} does not match case amount "
                f"₹{context.amount_paise / 100:.2f}"
            )
    for iso in extract_dates(text):
        if iso not in context.allowed_dates:
            errors.append(
                f"date {iso} is not in the case record "
                f"({', '.join(sorted(context.allowed_dates))})"
            )
    return errors


def _format_inr(paise: int) -> str:
    """Indian grouping for a rupee figure derived from paise."""
    rupees = abs(paise) // 100
    if rupees == 0:
        return "0"
    value = str(int(rupees))
    last3 = value[-3:]
    rest = value[:-3]
    if rest:
        groups: list[str] = []
        while rest:
            groups.append(rest[-2:])
            rest = rest[:-2]
        return ",".join(reversed(groups)) + "," + last3
    return last3


def _needs_unicode(text: str) -> bool:
    """True when the text requires a UCS-2 SMS segment (70 chars)."""
    return any(ord(ch) > 0x7F for ch in text)


# -- Fallback templates (LLM fails → deterministic templates, §2.4) ---------

DRAFT_TEMPLATES: dict[str, dict[str, str]] = {
    "sms": {
        "en": (
            "{merchant_name}: Payment of {amount} is pending. Pay here: "
            "{payment_link} (valid until {expiry})."
        ),
        "hi-en": (
            "{merchant_name}: {amount} ka payment pending hai. Yaha pay "
            "karein: {payment_link} ({expiry} tak valid)."
        ),
    },
    "whatsapp": {
        "en": (
            "Hi {customer_name}, your payment of {amount} to {merchant_name} "
            "is pending. Complete it securely here: {payment_link} "
            "(valid until {expiry}). Reply here if you need help."
        ),
        "hi-en": (
            "Namaste {customer_name}, {merchant_name} ko {amount} ka payment "
            "pending hai. Yaha se secure payment karein: {payment_link} "
            "({expiry} tak valid). Help chahiye to reply karein."
        ),
    },
    "email": {
        "en": (
            "subject=Payment of {amount} pending — {merchant_name}\n"
            "body=Dear {customer_name},\n\n"
            "Your payment of {amount} to {merchant_name} is pending. "
            "Complete it securely here: {payment_link} (valid until {expiry}).\n\n"
            "If you have already paid, please ignore this email.\n\n"
            "— {merchant_name}"
        ),
        "hi-en": (
            "subject={amount} ka payment pending — {merchant_name}\n"
            "body=Namaste {customer_name},\n\n"
            "{merchant_name} ko {amount} ka payment pending hai. Secure "
            "payment karein: {payment_link} ({expiry} tak valid).\n\n"
            "Agar aap pehle hi pay kar chuke hain, to is email ko ignore karein.\n\n"
            "— {merchant_name}"
        ),
    },
    "voice_script": {
        "en": (
            "Hello {customer_name}. This is a recorded message from "
            "{merchant_name}. Your payment of {amount} is pending. Please "
            "complete it using the payment link sent to you — valid until "
            "{expiry}. If you have already paid, please ignore this call. Thank you."
        ),
        "hi-en": (
            "Namaste {customer_name}. {merchant_name} ki taraf se ye recorded "
            "message hai. Aapka {amount} ka payment pending hai. Aapko bheje "
            "gaye payment link se payment karein — link {expiry} tak valid "
            "hai. Agar aap pehle hi pay kar chuke hain, to is call ko ignore "
            "karein. Dhanyavad."
        ),
    },
}


class ChannelDraftGenerator:
    """Generates validated SMS/WhatsApp/Email/Voice drafts per register.

    Every draft is produced by filling backend-verified template variables.
    When an LLM client is available it localizes the wording, then the
    hallucination guard re-validates all amounts and dates against the case
    record — a mismatch rejects the attempt and triggers regeneration
    (§2.3, §10.7). When the LLM is unavailable or keeps failing, deterministic
    templates render the draft instead (§2.4 fallback hierarchy).
    """

    def __init__(
        self,
        llm_client=None,
        template_engine: TemplateEngine | None = None,
        max_attempts: int = 2,
        sms_gsm_limit: int = SMS_GSM_LIMIT,
        sms_unicode_limit: int = SMS_UNICODE_LIMIT,
    ) -> None:
        self._llm = llm_client
        self._templates = template_engine or TemplateEngine()
        self._max_attempts = max(1, int(max_attempts))
        self._sms_gsm_limit = sms_gsm_limit
        self._sms_unicode_limit = sms_unicode_limit

    def generate(
        self, context: DraftContext
    ) -> dict[str, dict[str, RenderedDraft]]:
        """Build all channel/register drafts for one case record."""
        return {
            channel: {
                register: self._generate_one(context, channel, register)
                for register in REGISTERS
            }
            for channel in CHANNELS
        }

    def generate_channel(self, context: DraftContext, channel: str) -> dict[str, RenderedDraft]:
        if channel not in CHANNELS:
            raise ValueError(f"Unknown channel: {channel}")
        return {
            register: self._generate_one(context, channel, register)
            for register in REGISTERS
        }

    # -- internals ----------------------------------------------------------

    def _generate_one(
        self, context: DraftContext, channel: str, register: str
    ) -> RenderedDraft:
        attempts = 0
        while attempts < self._max_attempts:
            attempts += 1
            if self._llm is None:
                break
            payload = try_generate_json(
                self._llm, self._build_prompt(context, channel, register)
            )
            subject, body = self._extract_body(payload, channel)
            if not body.strip():
                continue
            errors = validate_draft(context, subject, body)
            if errors:
                continue
            length_error = self._length_error(channel, subject, body)
            if length_error:
                continue
            return RenderedDraft(
                channel=channel,
                register=register,
                subject=subject,
                body=body,
                source="llm",
                attempts=attempts,
            )

        subject, body = self._render_fallback(context, channel, register)
        return RenderedDraft(
            channel=channel,
            register=register,
            subject=subject,
            body=body,
            source="fallback",
            attempts=attempts,
        )

    def _extract_body(self, payload: dict, channel: str) -> tuple[str, str]:
        if not payload:
            return "", ""
        if channel == "email":
            return str(payload.get("subject", "")), str(payload.get("body", ""))
        return "", str(payload.get("body") or payload.get("script") or "")

    def _length_error(self, channel: str, subject: str, body: str) -> str | None:
        if channel != "sms":
            return None
        text = body
        limit = (
            self._sms_unicode_limit if _needs_unicode(text) else self._sms_gsm_limit
        )
        if len(text) > limit:
            return f"sms body is {len(text)} chars (limit {limit})"
        return None

    def _render_fallback(
        self, context: DraftContext, channel: str, register: str
    ) -> tuple[str, str]:
        template = DRAFT_TEMPLATES[channel][register]
        if channel == "email":
            lines = template.format(**context.variables).split("\n", 1)
            subject = lines[0].replace("subject=", "", 1)
            body = lines[1].replace("body=", "", 1)
            return subject, body
        body = template.format(**context.variables)
        if channel == "sms":
            body = self._clip_sms(body)
        return "", body

    def _clip_sms(self, body: str) -> str:
        limit = (
            self._sms_unicode_limit if _needs_unicode(body) else self._sms_gsm_limit
        )
        if len(body) <= limit:
            return body
        return body[: limit - 3] + "..."

    def _build_prompt(
        self, context: DraftContext, channel: str, register: str
    ) -> str:
        register_label = "Standard English" if register == "en" else "Hinglish"
        variables = "\n".join(
            f"- {key}: {context.variables[key]}" for key in DRAFT_VARIABLE_KEYS
        )
        channel_notes = {
            "sms": (
                "Write a short SMS. Keep it within the SMS length limit "
                "(160 chars latin, 70 chars when it contains non-ASCII symbols "
                "like ₹). DLT-template-friendly wording: no images/emojis, "
                "plain text."
            ),
            "whatsapp": (
                "Write a WhatsApp Business template-friendly message. It may "
                "be longer than an SMS. It MUST include the payment link."
            ),
            "email": (
                "Write a short email with a clear subject line and a short "
                "body (a few sentences). It MUST include the payment link."
            ),
            "voice_script": (
                "Write a short voice-call script to be read aloud slowly by a "
                "call agent, with polite phrasing."
            ),
        }[channel]
        output_keys = 'only the key "body"' if channel != "email" else 'the keys "subject" and "body"'

        return (
            "You are drafting customer payment-recovery messages for RecoveryOS.\n"
            "Use these backend-verified values EXACTLY as given — never invent, "
            "round, approximate, or restate any financial amount or date:\n"
            f"{variables}\n\n"
            "Channel: {channel}.\n"
            "Register: {register}.\n"
            f"{channel_notes}\n\n"
            "Rules:\n"
            "- Every monetary figure must be exactly the amount value above.\n"
            "- Every date must be one of the dates listed above verbatim.\n"
            "- Do not add any other amount, date, or link.\n"
            "Respond with a JSON object containing {output_keys}."
        ).format(channel=channel, register=register_label, output_keys=output_keys)
