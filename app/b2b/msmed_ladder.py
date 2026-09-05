"""MSMED Act 2006 escalation ladder — Module D B2B receivables (§9.4).

The 4-rung statutory escalation ladder is an EXPLICIT state machine over the
days-since-invoice timeline:

    Day 1–15     RUNG_1  gentle statement nudge
    Day 16–30    RUNG_2  §16 interest advisory
    Day 31–44    RUNG_3  formal demand notice
    Day 45+      RUNG_4  MSME Samadhaan conciliation filing

Day 1 is the invoice date; the §15/§16 45-day acceptance window therefore
ends on Day 45 and §16 interest accrues from Day 46 onward (see
``msmed_interest``).

Two hard rules are enforced here, not in the UI:

  * Rung 4 is a LEGAL filing. A **hard human sign-off gate** must approve the
    filing before it may be dispatched. There is no code path that auto-files
    a legal notice — ``request_conciliation_filing`` only produces a
    PENDING_SIGNOFF packet, and the filing record is created solely by an
    explicit ``approve``.
  * Buyers who are NOT counterparties of an MSME-registered supplier under
    the Act are labelled "standard commercial terms apply — §16 does not
    apply" and never run the interest calculation.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
from datetime import date, timedelta

from app.b2b.msmed_interest import (
    MsmedInterestCalculator,
    compute_interest_run,
)

STANDARD_TERMS_LABEL = "standard commercial terms apply — §16 does not apply"


class MsmedApplicability(str, enum.Enum):
    """Whether the MSMED Act 2006 §15/§16 recovery path applies."""

    APPLICABLE = "applicable"
    STANDARD_COMMERCIAL_TERMS = "standard_commercial_terms"


class StatutoryRung(str, enum.Enum):
    """One rung of the escalation ladder."""

    RUNG_1_NUDGE = "RUNG_1_NUDGE"
    RUNG_2_INTEREST_ADVISORY = "RUNG_2_INTEREST_ADVISORY"
    RUNG_3_DEMAND_NOTICE = "RUNG_3_DEMAND_NOTICE"
    RUNG_4_CONCILIATION_FILING = "RUNG_4_CONCILIATION_FILING"

    @property
    def number(self) -> int:
        return {"RUNG_1_NUDGE": 1, "RUNG_2_INTEREST_ADVISORY": 2,
                "RUNG_3_DEMAND_NOTICE": 3, "RUNG_4_CONCILIATION_FILING": 4}[self.value]

    @property
    def label(self) -> str:
        return {
            "RUNG_1_NUDGE": "Gentle statement nudge",
            "RUNG_2_INTEREST_ADVISORY": "§16 interest advisory",
            "RUNG_3_DEMAND_NOTICE": "Formal demand notice",
            "RUNG_4_CONCILIATION_FILING": "MSME Samadhaan conciliation filing",
        }[self.value]

    @property
    def has_statutory_notice(self) -> bool:
        """Rungs 2–4 carry a Gemini-drafted statutory notice (E.10)."""
        return self.number >= 2


# Day range (inclusive) for each rung. Day 1 = invoice date.
RUNG_DAY_RANGES: dict[StatutoryRung, tuple[int, int]] = {
    StatutoryRung.RUNG_1_NUDGE: (1, 15),
    StatutoryRung.RUNG_2_INTEREST_ADVISORY: (16, 30),
    StatutoryRung.RUNG_3_DEMAND_NOTICE: (31, 44),
    StatutoryRung.RUNG_4_CONCILIATION_FILING: (45, None),  # 45+ (no upper bound)
}


@dataclasses.dataclass(frozen=True)
class B2BReceivable:
    """One B2B invoice context needed by the §16 calculator and ladder."""

    case_id: str
    invoice_id: str
    buyer_name: str
    buyer_category: str  # "private" | "government" | "public_sector"
    invoice_date: date
    amount_paise: int
    supplier_msme_registered: bool  # §2(n) "supplier" / Udyam-registered MSME
    profile_source: str = "stored"  # "stored" | "demo_derived"


@dataclasses.dataclass(frozen=True)
class MsmedStatus:
    """Live statutory status for one receivable at one date."""

    case_id: str
    applicability: MsmedApplicability
    applicable_label: str  # human sentence when §16 does NOT apply
    days_since_invoice: int
    rung: StatutoryRung | None
    today: date
    receivable: B2BReceivable
    interest_paise: int
    total_claim_paise: int
    interest_rate_percent: float | None
    profile_source: str
    statutory_day_45: date
    accrual_start: date | None

    @property
    def statutory_applies(self) -> bool:
        return self.applicability is MsmedApplicability.APPLICABLE


@dataclasses.dataclass(frozen=True)
class LadderTransition:
    """One explicit rung transition (state-machine step)."""

    case_id: str
    from_rung: StatutoryRung
    to_rung: StatutoryRung
    days_since_invoice: int
    escalated: bool
    transitioned_at: date
    reason: str


class FilingState(str, enum.Enum):
    """Hard human sign-off state machine for the Rung-4 filing."""

    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING_SIGNOFF = "PENDING_SIGNOFF"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FILED = "FILED"


@dataclasses.dataclass(frozen=True)
class ConciliationFiling:
    """The Rung-4 filing packet — only ever dispatched after human sign-off."""

    case_id: str
    filing_reference: str
    state: FilingState
    requested_at: date
    approved_by: str | None = None
    approved_at: date | None = None
    remark: str | None = None
    dispatched: bool = False


def days_since_invoice(invoice_date: date, today: date) -> int:
    """Day count where the invoice date is Day 1."""
    return (today - invoice_date).days + 1


def rung_for(days: int) -> StatutoryRung:
    """Map a day count to its ladder rung."""
    if days <= 15:
        return StatutoryRung.RUNG_1_NUDGE
    if days <= 30:
        return StatutoryRung.RUNG_2_INTEREST_ADVISORY
    if days <= 44:
        return StatutoryRung.RUNG_3_DEMAND_NOTICE
    return StatutoryRung.RUNG_4_CONCILIATION_FILING


def applicable_label(reason: str) -> str:
    """Standard sentence emitted when §16 does not apply to a buyer."""
    return f"{STANDARD_TERMS_LABEL} ({reason})"


def stable_digest(value: str) -> int:
    """Deterministic stable int from a string (demo projection seeding)."""
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


class MsmedEscalationLadder:
    """The explicit 4-rung state machine (§9.4 / E.10)."""

    def __init__(
        self,
        calculator: MsmedInterestCalculator | None = None,
    ) -> None:
        self._calculator = calculator or MsmedInterestCalculator()

    # -- stateless queries -------------------------------------------------

    def rung(self, days: int) -> StatutoryRung:
        return rung_for(days)

    def day_range(self, rung: StatutoryRung) -> tuple[int, int | None]:
        return RUNG_DAY_RANGES[rung]

    def state_for(
        self,
        receivable: B2BReceivable,
        today: date | None = None,
    ) -> MsmedStatus:
        """Compute the live statutory status for a receivable at ``today``.

        ``today`` is injected by the caller; production passes the system
        clock, the E.12 demo clock passes its fast-forwarded date.
        """
        today = today or _real_today()
        days = days_since_invoice(receivable.invoice_date, today)
        day_45 = receivable.invoice_date + timedelta(days=45)

        if not receivable.supplier_msme_registered:
            return MsmedStatus(
                case_id=receivable.case_id,
                applicability=MsmedApplicability.STANDARD_COMMERCIAL_TERMS,
                applicable_label=applicable_label(
                    "supplier is not an MSME-registered (Udyam) supplier under §2(n)"
                ),
                days_since_invoice=days,
                rung=None,
                today=today,
                receivable=receivable,
                interest_paise=0,
                total_claim_paise=receivable.amount_paise,
                interest_rate_percent=None,
                profile_source=receivable.profile_source,
                statutory_day_45=day_45,
                accrual_start=None,
            )

        run = compute_interest_run(
            receivable.amount_paise,
            receivable.invoice_date,
            today,
            bank_rate_percent=None,
        )
        return MsmedStatus(
            case_id=receivable.case_id,
            applicability=MsmedApplicability.APPLICABLE,
            applicable_label="",
            days_since_invoice=days,
            rung=rung_for(days),
            today=today,
            receivable=receivable,
            interest_paise=run.accrued_interest_paise,
            total_claim_paise=run.total_statutory_claim_paise,
            interest_rate_percent=run.statutory_rate_percent,
            profile_source=receivable.profile_source,
            statutory_day_45=day_45,
            accrual_start=run.accrual_start_date,
        )

    # -- explicit transition ------------------------------------------------

    def advance(
        self,
        receivable: B2BReceivable,
        today: date | None = None,
    ) -> LadderTransition:
        """Emerge the current rung from the previous day's rung.

        This is the single state-machine step: given the same receivable the
        day before, whichever rung Calendar(today) resolves to is the onward
        transition. Rungs never go backwards on the statutory clock.
        """
        today = today or _real_today()
        days = days_since_invoice(receivable.invoice_date, today)
        current = rung_for(days)
        previous_days = max(0, days - 1)
        previous = rung_for(previous_days)
        return LadderTransition(
            case_id=receivable.case_id,
            from_rung=previous,
            to_rung=current,
            days_since_invoice=days,
            escalated=current.number > previous.number,
            transitioned_at=today,
            reason=(
                f"Statutory escalation to {current.label} on day {days} "
                f"(invoice {receivable.invoice_date.isoformat()})"
            ),
        )

    # -- Rung-4 human sign-off gate ----------------------------------------

    def request_conciliation_filing(
        self,
        receivable: B2BReceivable,
        today: date | None = None,
    ) -> ConciliationFiling:
        """Request a Rung-4 filing — NEVER auto-files.

        Only valid from Day 45+. Produces a PENDING_SIGNOFF packet that an
        authorized human must approve or reject before anything is dispatched.
        """
        today = today or _real_today()
        days = days_since_invoice(receivable.invoice_date, today)
        if days < 45:
            raise ValueError(
                f"Conciliation filing requires Day 45+ (this case is Day {days}) "
                "— §18 conciliation is not available inside the §15 acceptance window"
            )
        return ConciliationFiling(
            case_id=receivable.case_id,
            filing_reference=f"SAMADHAAN-{receivable.case_id}-{today.strftime('%Y%m%d')}",
            state=FilingState.PENDING_SIGNOFF,
            requested_at=today,
        )

    def approve_conciliation_filing(
        self,
        filing: ConciliationFiling,
        *,
        approved_by: str,
        today: date | None = None,
    ) -> ConciliationFiling:
        """Human sign-off. Only PENDING_SIGNOFF filings can be approved."""
        today = today or _real_today()
        if filing.state is not FilingState.PENDING_SIGNOFF:
            raise ValueError(
                f"Filing {filing.filing_reference} is {filing.state.value}, not PENDING_SIGNOFF"
            )
        return dataclasses.replace(
            filing,
            state=FilingState.APPROVED,
            approved_by=approved_by,
            approved_at=today,
        )

    def dispatch_conciliation_filing(
        self,
        filing: ConciliationFiling,
        today: date | None = None,
    ) -> ConciliationFiling:
        """Dispatch the filing to MSME Samadhaan — ONLY after human sign-off.

        This is the only path that marks a filing dispatched, and it refuses
        any filing that is not APPROVED. There is no auto-file code path.
        """
        today = today or _real_today()
        if filing.state is not FilingState.APPROVED:
            raise ValueError(
                f"Filing {filing.filing_reference} is {filing.state.value}, not APPROVED — "
                "human sign-off is mandatory before dispatch"
            )
        return dataclasses.replace(
            filing,
            state=FilingState.FILED,
            dispatched=True,
            approved_at=filing.approved_at or today,
        )


class MsmedFilingRegistry:
    """Per-case filing state owned by the ladder (keyed by case_id)."""

    def __init__(self) -> None:
        self._filings: dict[str, ConciliationFiling] = {}

    def get(self, case_id: str) -> ConciliationFiling | None:
        return self._filings.get(case_id)

    def record(self, filing: ConciliationFiling) -> None:
        self._filings[filing.case_id] = filing

    def approve(self, case_id: str, *, approved_by: str, today: date | None = None) -> ConciliationFiling:
        current = self._filings.get(case_id)
        if current is None:
            raise KeyError(f"No conciliation filing requested for case_id={case_id}")
        if current.state is not FilingState.PENDING_SIGNOFF:
            raise ValueError(
                f"Filing {current.filing_reference} is {current.state.value}, "
                "only PENDING_SIGNOFF can be approved"
            )
        approved = dataclasses.replace(
            current,
            state=FilingState.APPROVED,
            approved_by=approved_by,
            approved_at=today or _real_today(),
        )
        self._filings[case_id] = approved
        return approved

    def reject(self, case_id: str, *, remark: str, today: date | None = None) -> ConciliationFiling:
        current = self._filings.get(case_id)
        if current is None:
            raise KeyError(f"No conciliation filing requested for case_id={case_id}")
        if current.state is not FilingState.PENDING_SIGNOFF:
            raise ValueError(
                f"Filing {current.filing_reference} is {current.state.value}, "
                "only PENDING_SIGNOFF can be rejected"
            )
        rejected = dataclasses.replace(
            current,
            state=FilingState.REJECTED,
            remark=remark,
            approved_at=today or _real_today(),
        )
        self._filings[case_id] = rejected
        return rejected

    def dispatch(self, case_id: str, today: date | None = None) -> ConciliationFiling:
        current = self._filings.get(case_id)
        if current is None:
            raise KeyError(f"No conciliation filing requested for case_id={case_id}")
        if current.state is not FilingState.APPROVED:
            raise ValueError(
                f"Filing {current.filing_reference} is {current.state.value} — "
                "human sign-off (APPROVED) is mandatory before dispatch"
            )
        dispatched = dataclasses.replace(
            current,
            state=FilingState.FILED,
            approved_at=current.approved_at or today or _real_today(),
            dispatched=True,
        )
        self._filings[case_id] = dispatched
        return dispatched


def _real_today() -> date:
    """System-clock date (E.12 swaps this for the demo clock)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date()