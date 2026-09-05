"""MSMED Act 2006 §16 compound-interest calculator (§9.4 Module D).

Section 16 of the Micro, Small and Medium Enterprises Development Act, 2006
requires the buyer to pay interest on an overdue amount to an MSME supplier:

  * interest accrues from the day AFTER the 45-day acceptance period; and
  * the rate is THREE TIMES the bank rate notified by the Reserve Bank of
    India, compounded with MONTHLY RESTS.

This module is the live interest engine. Nothing here is frozen at
generation time: ``accrued_interest_to_date`` and ``total_statutory_claim``
re-derive the figure from the supplied ``today`` (the caller decides the
"now" — production uses the system clock, the E.12 demo clock otherwise).

Conventions (documented, deterministic, hand-auditable):
  * a "month" for monthly rests is 30 days;
  * interest first accrues on ``invoice_date + 46 days`` (the day after the
    statutory 45-day window), inclusive;
  * each completed 30-day month compounds the full outstanding balance
    (principal + previously rolled interest) at the monthly rate;
  * a trailing partial month accrues simple interest pro-rata (<1 full month);
  * every compounding step is rounded to the nearest paise.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

# §16: 45-day acceptance window for supply of goods/services.
MSMED_STATUTORY_DAYS = 45

# §16: interest at three times the RBI-notified bank rate.
MSMED_INTEREST_MULTIPLE = 3

# RBI-notified bank rate (per cent per annum) used when the caller does not
# supply a configured value. Kept in one place so the reference is auditable.
DEFAULT_RBI_BANK_RATE_PERCENT = 6.25  # % p.a.


@dataclasses.dataclass(frozen=True)
class InterestRun:
    """A fully recomputed §16 interest run for one receivable at one date."""

    principal_paise: int
    invoice_date: date
    today: date
    bank_rate_percent: float
    statutory_rate_percent: float  # 3 × bank rate
    monthly_rate_percent: float  # statutory ÷ 12
    accrual_start_date: date  # day after the 45-day acceptance window
    elapsed_days: int  # inclusive accrual days so far (0 when not yet applying)
    months_compounded: int  # completed 30-day rests
    partial_days: int  # trailing partial rest (0 when none)
    accrued_interest_paise: int
    total_statutory_claim_paise: int

    @property
    def applies(self) -> bool:
        """True when §16 interest is actually running (>45 days after invoice)."""
        return self.elapsed_days > 0

    @property
    def interest_inr(self) -> float:
        return self.accrued_interest_paise / 100

    @property
    def claim_inr(self) -> float:
        return self.total_statutory_claim_paise / 100


def statutory_rate_percent(bank_rate_percent: float | None) -> float:
    """§16 rate = three times the RBI bank rate, as % p.a."""
    base = bank_rate_percent if bank_rate_percent is not None else DEFAULT_RBI_BANK_RATE_PERCENT
    if base <= 0:
        raise ValueError("RBI bank rate must be positive")
    return base * MSMED_INTEREST_MULTIPLE


def monthly_rate_percent(bank_rate_percent: float | None) -> float:
    """Monthly-rest rate for one 30-day month."""
    return statutory_rate_percent(bank_rate_percent) / 12


def accrual_start_date(invoice_date: date) -> date:
    """First day §16 interest accrues = the day after the 45-day window."""
    return invoice_date + timedelta(days=MSMED_STATUTORY_DAYS + 1)


def accrual_elapsed_days(invoice_date: date, today: date) -> int:
    """Inclusive number of accrual days to date (0 while still inside §15 window)."""
    start = accrual_start_date(invoice_date)
    if today < start:
        return 0
    return (today - start).days + 1


def _round_paise(value: float) -> int:
    """Round a float rupee-amount to the nearest paise."""
    return round(value)


def compute_interest_run(
    principal_paise: int,
    invoice_date: date,
    today: date,
    bank_rate_percent: float | None = None,
) -> InterestRun:
    """Compute the full §16 interest run for one receivable.

    Live by construction: pass the date you want the exposure for. In
    production that is the system clock; the E.12 demo clock passes its
    fast-forwarded "now".
    """
    if principal_paise < 0:
        raise ValueError("principal_paise must be non-negative")

    stat_rate = statutory_rate_percent(bank_rate_percent)
    month_rate = stat_rate / 100 / 12  # monthly-rest decimal factor

    elapsed = accrual_elapsed_days(invoice_date, today)
    months, partial = divmod(elapsed, 30)

    balance = float(principal_paise)
    for _ in range(months):
        balance += _round_paise(balance * month_rate)
    if partial:
        balance += _round_paise(balance * month_rate * partial / 30)

    total = _round_paise(balance)
    interest = total - principal_paise

    return InterestRun(
        principal_paise=principal_paise,
        invoice_date=invoice_date,
        today=today,
        bank_rate_percent=bank_rate_percent if bank_rate_percent is not None else DEFAULT_RBI_BANK_RATE_PERCENT,
        statutory_rate_percent=stat_rate,
        monthly_rate_percent=stat_rate / 12,
        accrual_start_date=accrual_start_date(invoice_date),
        elapsed_days=elapsed,
        months_compounded=months,
        partial_days=partial,
        accrued_interest_paise=interest,
        total_statutory_claim_paise=total,
    )


class MsmedInterestCalculator:
    """Stateless wrapper over :func:`compute_interest_run`.

    Exposes the two live entry points E.10/E.12 call:
      * ``accrued_interest_to_date(...)``
      * ``total_statutory_claim(...)``
    """

    def __init__(self, bank_rate_percent: float | None = None) -> None:
        self._bank_rate = bank_rate_percent

    def accrued_interest_to_date(
        self,
        principal_paise: int,
        invoice_date: date,
        today: date | None = None,
    ) -> int:
        run = compute_interest_run(
            principal_paise,
            invoice_date,
            today or _real_today(),
            bank_rate_percent=self._bank_rate,
        )
        return run.accrued_interest_paise

    def total_statutory_claim(
        self,
        principal_paise: int,
        invoice_date: date,
        today: date | None = None,
    ) -> int:
        run = compute_interest_run(
            principal_paise,
            invoice_date,
            today or _real_today(),
            bank_rate_percent=self._bank_rate,
        )
        return run.total_statutory_claim_paise

    def run(
        self,
        principal_paise: int,
        invoice_date: date,
        today: date | None = None,
    ) -> InterestRun:
        return compute_interest_run(
            principal_paise,
            invoice_date,
            today or _real_today(),
            bank_rate_percent=self._bank_rate,
        )


def _real_today() -> date:
    """System-clock date. The E.12 demo clock replaces this call site."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date()