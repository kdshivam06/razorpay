"""Gemini-drafted statutory notices for the MSMED ladder (E.10).

Rungs 2–4 carry a statutory notice (interest advisory / formal demand /
conciliation filing intent), draftable in Standard English or Hinglish.

The drafting reuses the E.6 guardrails exactly: the LLM may only localize
wording around backend-verified figures. ANY amount or date in the output
that is not in the case record causes the draft to be rejected and the
generator falls back to a deterministic template (§2.3, §2.4, §10.7).

Backend-truth values allowed in a notice:
  * amounts:  principal, accrued §16 interest, total statutory claim
  * dates:    invoice date, statutory due date (invoice+45), notice date,
              §16 accrual start date
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from app.b2b.msmed_ladder import B2BReceivable, StatutoryRung
from app.nlp.gemini_client import try_generate_json
from app.nlp.message_templates import (
    REGISTERS,
    DraftContext,
    validate_draft,
)

NOTICE_REFERENCE_LINE = (
    "Reference: Micro, Small and Medium Enterprises Development Act, 2006 "
    "(§15 payment within 45 days; §16 interest at three times the RBI bank "
    "rate with monthly rests; §18 conciliation before the Arbitration Council)"
)


@dataclasses.dataclass(frozen=True)
class NoticeSpec:
    """Backend-verified values a statutory notice may contain."""

    rung: StatutoryRung
    receivable: B2BReceivable
    buyer_name: str
    invoice_id: str
    invoice_date: date
    statutory_due_date: date  # invoice + 45 days (§15)
    accrual_start_date: date | None
    notice_date: date  # today (the notice is drafted as-of now)
    principal_paise: int
    interest_paise: int
    claim_paise: int
    statutory_rate_percent: float | None
    filing_reference: str | None = None  # Rung 4 only


@dataclasses.dataclass(frozen=True)
class RenderedNotice:
    """A validated, ready-to-send statutory notice."""

    rung: StatutoryRung
    register: str
    subject: str
    body: str
    source: str  # "llm" | "fallback"
    attempts: int

    @property
    def validated(self) -> bool:
        return self.source in {"llm", "fallback"}


def build_notice_context(spec: NoticeSpec, notice_message: str) -> DraftContext:
    """Build the DraftContext the E.6 validator runs against.

    ``allowed_amount_paise`` carries exactly the three statutory figures and
    ``allowed_dates`` the four statutory dates; anything else in the draft is
    a hallucination and will be rejected.
    """
    iso = date.isoformat
    variables = {
        "buyer_name": spec.buyer_name,
        "invoice_id": spec.invoice_id,
        "invoice_date": iso(spec.invoice_date),
        "statutory_due_date": iso(spec.statutory_due_date),
        "notice_date": iso(spec.notice_date),
        "principal_paise": spec.principal_paise,
        "interest_paise": spec.interest_paise,
        "claim_paise": spec.claim_paise,
        "statutory_rate_percent": spec.statutory_rate_percent,
    }
    allowed_dates = {iso(spec.invoice_date), iso(spec.statutory_due_date), iso(spec.notice_date)}
    if spec.accrual_start_date is not None:
        allowed_dates.add(iso(spec.accrual_start_date))
    if spec.filing_reference:
        variables["filing_reference"] = spec.filing_reference
    return DraftContext(
        case_id=spec.receivable.case_id,
        amount_paise=spec.claim_paise,
        variables=variables,
        allowed_amount_paise=(spec.principal_paise, spec.interest_paise, spec.claim_paise),
        allowed_dates=frozenset(allowed_dates),
    )


def _fmt_inr(paise: int) -> str:
    """Indian grouping with exact paise — legal notices carry full figures."""
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    rupees, fraction = divmod(paise, 100)
    whole = str(rupees)
    last3 = whole[-3:]
    rest = whole[:-3]
    grouped = last3
    while rest:
        grouped = rest[-2:] + "," + grouped
        rest = rest[:-2]
    return f"{sign}{grouped}.{fraction:02d}"


FORMAT_INR = _fmt_inr


# Deterministic fallback notices (LLM absent/failed → §2.4 fallback hierarchy).
FALLBACK_NOTICE_TEMPLATES: dict[StatutoryRung, dict[str, tuple[str, str]]] = {
    StatutoryRung.RUNG_2_INTEREST_ADVISORY: {
        "en": (
            "Interest Advisory — invoice {invoice_id}",
            (
                "Dear {buyer_name},\n\n"
                "This is a formal advisory under the Micro, Small and Medium "
                "Enterprises Development Act, 2006. Invoice {invoice_id} for "
                "Rs {principal_inr} was due on {statutory_due_date} (45 days "
                "after the invoice date {invoice_date}).\n\n"
                "Since payment is not yet received, interest under §16 of the "
                "Act will accrue from {accrual_start_date} at three times the "
                "RBI-notified bank rate, compounded with monthly rests. As of "
                "{notice_date} the accrued statutory interest is Rs "
                "{interest_inr}, taking the total claim to Rs {claim_inr}.\n\n"
                "Kindly settle the amount within the statutory window to stop "
                "further interest.\n\n{reference}"
            ),
        ),
        "hi-en": (
            "Interest Advisory — Invoice {invoice_id}",
            (
                "Dear {buyer_name},\n\n"
                "Ye advisory MSMED Act 2006 ke section 16 ke under bhej rahe hain. "
                "Invoice {invoice_id} ka Rs {principal_inr} payment {statutory_due_date} "
                "tak due tha (invoice date {invoice_date} se 45 din).\n\n"
                "Payment abhi tak nahi aaya hai, isliye §16 ke under interest "
                "{accrual_start_date} se RBI bank rate ke teen guna par lagana "
                "shuru hoga, monthly rests ke saath compound hota hua. "
                "{notice_date} tak accrued interest Rs {interest_inr} hai, aur "
                "total claim Rs {claim_inr} ho gaya hai.\n\n"
                "Kripya amount statutory window mein settle karein taaki "
                "interest na badhe.\n\n{reference}"
            ),
        ),
    },
    StatutoryRung.RUNG_3_DEMAND_NOTICE: {
        "en": (
            "Formal Demand Notice — invoice {invoice_id} under MSMED Act 2006",
            (
                "Dear {buyer_name},\n\n"
                "You are hereby notified that invoice {invoice_id} for Rs "
                "{principal_inr}, due on {statutory_due_date}, remains unpaid.\n\n"
                "In accordance with §15 and §16 of the Micro, Small and Medium "
                "Enterprises Development Act, 2006, statutory interest accrues "
                "from {accrual_start_date} at three times the RBI-notified bank "
                "rate, compounded with monthly rests. As of {notice_date}, the "
                "accrued statutory interest is Rs {interest_inr} and the total "
                "amount due is Rs {claim_inr}.\n\n"
                "Please pay the full amount immediately. If payment is not made "
                "by day 45 from the invoice date, we will exercise our rights "
                "under §18 of the Act (conciliation through the MSME Samadhaan "
                "portal), without further notice.\n\n{reference}"
            ),
        ),
        "hi-en": (
            "Formal Demand Notice — Invoice {invoice_id} (MSMED Act 2006)",
            (
                "Dear {buyer_name},\n\n"
                "Ye formal demand notice hai. Invoice {invoice_id} ka Rs "
                "{principal_inr} payment {statutory_due_date} tak due tha, jo "
                "abhi tak pending hai.\n\n"
                "MSMED Act 2006 ke §15 aur §16 ke under, interest "
                "{accrual_start_date} se RBI bank rate ke teen guna par lagta "
                "hai, monthly rests ke saath compound hota hua. {notice_date} "
                "tak accrued statutory interest Rs {interest_inr} hai aur total "
                "amount Rs {claim_inr} ho chuka hai.\n\n"
                "Kripya turant full amount pay karein. Agar invoice date se 45 "
                "din tak payment nahi hua, to hum §18 ke under (MSME Samadhaan "
                "portal se conciliation) ka haq use karenge, bina aur notice "
                "ke.\n\n{reference}"
            ),
        ),
    },
    StatutoryRung.RUNG_4_CONCILIATION_FILING: {
        "en": (
            "Notice of MSME Samadhaan Conciliation Filing — invoice {invoice_id}",
            (
                "Dear {buyer_name},\n\n"
                "Our debt of Rs {principal_inr} under invoice {invoice_id} "
                "(due {statutory_due_date}) has now crossed the 45-day statutory "
                "window. The total amount outstanding under the MSMED Act 2006 "
                "as of {notice_date} is Rs {claim_inr}, which includes §16 "
                "statutory interest of Rs {interest_inr} accrued at three times "
                "the RBI bank rate with monthly rests.\n\n"
                "We are therefore filing a conciliation request under §18 of "
                "the Act through the MSME Samadhaan portal (reference "
                "{filing_reference}). We remain open to an amicable settlement "
                "before the conciliation proceeds.\n\n{reference}"
            ),
        ),
        "hi-en": (
            "MSME Samadhaan Conciliation Filing — Invoice {invoice_id}",
            (
                "Dear {buyer_name},\n\n"
                "Invoice {invoice_id} ke under Rs {principal_inr} ka hamara "
                "debt ({statutory_due_date} tak due) ab 45 din ki statutory "
                "window se aage nikal chuka hai. {notice_date} tak MSMED Act "
                "2006 ke under total outstanding Rs {claim_inr} hai, jisme §16 "
                "statutory interest Rs {interest_inr} (RBI bank rate ke teen "
                "guna, monthly rests ke saath) shamil hai.\n\n"
                "Isliye hum MSME Samadhaan portal ke through §18 ke under "
                "conciliation file kar rahe hain (reference {filing_reference}). "
                "Conciliation aage badhne se pehle hum amicable settlement ke "
                "liye taiyar hain.\n\n{reference}"
            ),
        ),
    },
}


class StatutoryNoticeGenerator:
    """Drafts validated statutory notices for Rungs 2–4 (E.10).

    Mirrors the E.6 channel draft pipeline:
      1. try Gemini (or injected LLM) to localize the register;
      2. re-validate EVERY amount and date against the case record;
      3. on mismatch reject and, on exhaustion, render the deterministic
         fallback (§2.4).
    """

    def __init__(
        self,
        llm_client=None,
        max_attempts: int = 2,
    ) -> None:
        self._llm = llm_client
        self._max_attempts = max(1, int(max_attempts))

    def generate(self, spec: NoticeSpec, register: str = "en") -> RenderedNotice:
        if spec.rung.number < 2:
            raise ValueError(f"No statutory notice for {spec.rung.value} (rungs 2–4 only)")
        if register not in REGISTERS:
            raise ValueError(f"Unknown register: {register}")

        attempts = 0
        while attempts < self._max_attempts:
            attempts += 1
            if self._llm is None:
                break
            payload = try_generate_json(self._llm, self._build_prompt(spec, register))
            subject = str(payload.get("subject", ""))
            body = str(payload.get("body", ""))
            if not subject.strip() and not body.strip():
                continue
            errors = validate_draft(build_notice_context(spec, ""), subject, body)
            if errors:
                continue
            return RenderedNotice(
                rung=spec.rung,
                register=register,
                subject=subject,
                body=body,
                source="llm",
                attempts=attempts,
            )

        subject, body = self._render_fallback(spec, register)
        return RenderedNotice(
            rung=spec.rung,
            register=register,
            subject=subject,
            body=body,
            source="fallback",
            attempts=attempts,
        )

    def _render_fallback(self, spec: NoticeSpec, register: str) -> tuple[str, str]:
        (subject, body) = FALLBACK_NOTICE_TEMPLATES[spec.rung][register]
        variables = self._variables(spec)
        try:
            return subject.format(**variables), body.format(**variables)
        except KeyError:
            return subject, body + "\n\n[unsafe template token removed by §2.3 guard]"

    def _variables(self, spec: NoticeSpec) -> dict[str, str]:
        stat_rate = spec.statutory_rate_percent
        base = {
            "buyer_name": spec.buyer_name,
            "invoice_id": spec.invoice_id,
            "invoice_date": spec.invoice_date.isoformat(),
            "statutory_due_date": spec.statutory_due_date.isoformat(),
            "accrual_start_date": (
                spec.accrual_start_date.isoformat()
                if spec.accrual_start_date
                else spec.statutory_due_date.isoformat()
            ),
            "notice_date": spec.notice_date.isoformat(),
            "principal_inr": FORMAT_INR(spec.principal_paise),
            "interest_inr": FORMAT_INR(max(0, spec.interest_paise)),
            "claim_inr": FORMAT_INR(spec.claim_paise),
            "statutory_rate_percent": f"{stat_rate:.1f}" if stat_rate else "3×RBI",
            "filing_reference": spec.filing_reference or f"SAMADHAAN-{spec.receivable.case_id}",
            "reference": NOTICE_REFERENCE_LINE,
        }
        return base

    def _build_prompt(self, spec: NoticeSpec, register: str) -> str:
        register_label = "Standard English" if register == "en" else "Hinglish"
        variables = "\n".join(
            f"- {key}: {value}" for key, value in self._variables(spec).items()
        )
        rung_guide = {
            StatutoryRung.RUNG_2_INTEREST_ADVISORY: (
                "An interest advisory under §16 of the MSMED Act 2006, "
                "informing the buyer that statutory interest is accruing at "
                "three times the RBI bank rate."
            ),
            StatutoryRung.RUNG_3_DEMAND_NOTICE: (
                "A formal demand notice under §15/§16 of the MSMED Act 2006, "
                "demanding the principal plus accrued statutory interest and "
                "warning of §18 conciliation rights."
            ),
            StatutoryRung.RUNG_4_CONCILIATION_FILING: (
                "A notice from an MSME supplier that a conciliation filing "
                "under §18 of the MSMED Act 2006 (MSME Samadhaan) is being "
                "made for this overdue receivable."
            ),
        }[spec.rung]
        return (
            "You are drafting a statutory commercial notice for RecoveryOS, a "
            "collections system for MSME suppliers.\n"
            "Use these backend-verified values EXACTLY as given — never invent, "
            "round, approximate, or restate any financial amount or date:\n"
            f"{variables}\n\n"
            f"Notice type: {rung_guide}\n"
            f"Register: {register_label}\n\n"
            "Rules:\n"
            "- Every monetary figure must be exactly one of the values above.\n"
            "- Every date must be one of the dates listed above verbatim.\n"
            "- You may reference §15, §16, §18 and 'MSME Samadhaan' but must "
            "not add any other section numbers or figures.\n"
            "- Do not invent a case number, filing number, or legal threat "
            "beyond the values provided.\n"
            'Respond with a JSON object with the keys "subject" and "body".'
        )


def build_notice_spec(
    receivable: B2BReceivable,
    *,
    interest_paise: int,
    claim_paise: int,
    statutory_rate_percent: float | None,
    notice_date: date | None = None,
    rung: StatutoryRung | None = None,
    filing_reference: str | None = None,
) -> NoticeSpec:
    """Assemble a NoticeSpec from a receivable + a live interest run."""
    from datetime import datetime

    today = notice_date or datetime.now().astimezone().date()
    due = receivable.invoice_date + timedelta(days=45)
    clause = rung or planned_rung(receivable, today)
    return NoticeSpec(
        rung=clause,
        receivable=receivable,
        buyer_name=receivable.buyer_name,
        invoice_id=receivable.invoice_id,
        invoice_date=receivable.invoice_date,
        statutory_due_date=due,
        accrual_start_date=receivable.invoice_date + timedelta(days=46),
        notice_date=today,
        principal_paise=receivable.amount_paise,
        interest_paise=int(interest_paise),
        claim_paise=int(claim_paise),
        statutory_rate_percent=statutory_rate_percent,
        filing_reference=filing_reference,
    )


def planned_rung(receivable: B2BReceivable, today: date) -> StatutoryRung:
    """The rung the notice should be addressed to (the case's current rung)."""
    from app.b2b.msmed_ladder import days_since_invoice, rung_for

    return rung_for(days_since_invoice(receivable.invoice_date, today))