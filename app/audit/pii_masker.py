"""PII masking (§16.1, §13.6, §10.6)."""

from __future__ import annotations


class PiiMasker:
    """Masks personally-identifiable information (PII) in logs, transcripts,
    and dashboard output so sensitive contact data never leaks to screens."""

    def mask_phone(self, value: str) -> str:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §13.6")

    def mask_email(self, value: str) -> str:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §13.6")

    def mask_text(self, text: str) -> str:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §13.6, §10.6")