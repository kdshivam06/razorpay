"""PII masking (§16.1, §13.6, §10.6).

Masks personally-identifiable information in logs, transcripts, and
dashboard output so sensitive contact data never leaks to screens.

Handles:
  - Phone numbers (Indian +91 and international)
  - Email addresses
  - PAN / Aadhaar numbers
  - Card numbers
  - Free-form text containing PII
"""

from __future__ import annotations

import re

# Compiled patterns for performance
_PHONE_PATTERN = re.compile(
    r"(\+?\d{1,3}[-.\s]?)?\(?\d{3,5}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}"
)
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PAN_PATTERN = re.compile(r"[A-Z]{5}\d{4}[A-Z]")
_AADHAAR_PATTERN = re.compile(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}")
_CARD_PATTERN = re.compile(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}")


class PiiMasker:
    """Masks personally-identifiable information (PII) in logs, transcripts,
    and dashboard output so sensitive contact data never leaks to screens.

    §10.6: Wrong-Person Protection — NO PAYMENT DISCLOSURE.
    """

    def mask_phone(self, value: str) -> str:
        """Mask a phone number, keeping last 4 digits.

        +919876543210 → +91******3210
        """
        if not value:
            return value
        digits = re.sub(r"[^\d]", "", value)
        if len(digits) < 4:
            return "****"
        return f"{'*' * (len(digits) - 4)}{digits[-4:]}"

    def mask_email(self, value: str) -> str:
        """Mask an email address, keeping first char and domain.

        user@example.com → u***@example.com
        """
        if not value or "@" not in value:
            return "***@***"
        local, domain = value.rsplit("@", 1)
        if len(local) <= 1:
            masked_local = local
        else:
            masked_local = f"{local[0]}{'*' * (len(local) - 1)}"
        return f"{masked_local}@{domain}"

    def mask_pan(self, value: str) -> str:
        """Mask a PAN number, keeping first 2 and last char.

        ABCDE1234F → AB***234F
        """
        if not value or len(value) < 10:
            return "**********"
        return f"{value[:2]}{'*' * 5}{value[-3:]}"

    def mask_aadhaar(self, value: str) -> str:
        """Mask an Aadhaar number, keeping last 4 digits.

        1234 5678 9012 → **** **** 9012
        """
        digits = re.sub(r"[^\d]", "", value)
        if len(digits) < 4:
            return "**** **** ****"
        return f"**** **** {digits[-4:]}"

    def mask_card(self, value: str) -> str:
        """Mask a card number, keeping last 4 digits.

        4111 1111 1111 1111 → **** **** **** 1111
        """
        digits = re.sub(r"[^\d]", "", value)
        if len(digits) < 4:
            return "**** **** **** ****"
        return f"**** **** **** {digits[-4:]}"

    def mask_text(self, text: str) -> str:
        """Mask all PII detected in free-form text.

        Handles phone numbers, emails, PAN, Aadhaar, and card numbers.
        """
        if not text:
            return text

        result = text

        # Mask card numbers first (longest pattern)
        result = _CARD_PATTERN.sub(lambda m: self.mask_card(m.group()), result)

        # Mask Aadhaar (12 digits, possibly with spaces/dashes)
        result = _AADHAAR_PATTERN.sub(lambda m: self.mask_aadhaar(m.group()), result)

        # Mask PAN
        result = _PAN_PATTERN.sub(lambda m: self.mask_pan(m.group()), result)

        # Mask emails
        result = _EMAIL_PATTERN.sub(lambda m: self.mask_email(m.group()), result)

        # Mask phone numbers
        result = _PHONE_PATTERN.sub(lambda m: self.mask_phone(m.group()), result)

        return result
