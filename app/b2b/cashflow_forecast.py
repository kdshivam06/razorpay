"""B2B weekly cashflow forecast (§9.4).

Expected cash this week = ₹24.3L
Expected late           = ₹4.8L
High-risk               = ₹2.1L
"""

from __future__ import annotations

import dataclasses
import logging

from app.core.recovery_case import RecoveryCase

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CashflowForecast:
    """Expected weekly cash inflow with late/high-risk breakdown (§9.4)."""

    expected_cash_paise: int
    expected_late_paise: int
    high_risk_paise: int
    on_time_paise: int = 0
    total_outstanding_paise: int = 0
    num_invoices: int = 0

    @property
    def expected_cash_display(self) -> str:
        return f"₹{self.expected_cash_paise / 100:,.0f}"

    @property
    def collection_rate(self) -> float:
        if self.total_outstanding_paise == 0:
            return 0.0
        return self.expected_cash_paise / self.total_outstanding_paise


class CashflowForecaster:
    """Predicts expected B2B cash this week + late + high-risk (§9.4).

    Uses per-case natural payment probability and uplift segment to
    estimate which invoices will pay on time, late, or not at all.
    """

    def forecast(self, cases: list[RecoveryCase]) -> CashflowForecast:
        """Forecast weekly cashflow from a list of active B2B cases."""
        total_outstanding = 0
        expected_cash = 0
        expected_late = 0
        high_risk = 0
        on_time = 0

        for case in cases:
            remaining = case.total_remaining()
            total_outstanding += remaining
            prob = case.natural_pay_probability

            if prob >= 0.8:
                # Likely to pay on time
                on_time += remaining
                expected_cash += int(remaining * prob)
            elif prob >= 0.4:
                # Likely to pay late
                expected_late += int(remaining * prob)
                expected_cash += int(remaining * prob * 0.7)
            else:
                # High risk — may not pay without intervention
                high_risk += remaining
                expected_cash += int(remaining * prob * 0.3)

        forecast = CashflowForecast(
            expected_cash_paise=expected_cash,
            expected_late_paise=expected_late,
            high_risk_paise=high_risk,
            on_time_paise=on_time,
            total_outstanding_paise=total_outstanding,
            num_invoices=len(cases),
        )

        logger.info(
            "B2B cashflow forecast: expected=%s, late=%s, high_risk=%s "
            "(%d invoices, collection_rate=%.0f%%)",
            forecast.expected_cash_display,
            f"₹{expected_late / 100:,.0f}",
            f"₹{high_risk / 100:,.0f}",
            len(cases),
            forecast.collection_rate * 100,
        )
        return forecast
