"""B2B weekly cashflow forecast (§9.4)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class CashflowForecast:
    """Expected weekly cash inflow with late/high-risk breakdown (§9.4)."""

    expected_cash_paise: int
    expected_late_paise: int
    high_risk_paise: int


class CashflowForecaster:
    """Predicts expected cash this week + late + high-risk (§9.4)."""

    def forecast(self, client_ids: list[str]) -> CashflowForecast:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.4")
