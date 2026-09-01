"""Time-to-payment model — expected days (and median) to payment (§5.2)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


class TimeToPaymentModel:
    """Predicts WHEN a case pays, not just whether (§5.2)."""

    def __init__(self, artifact_path: Path | None = None) -> None:
        raise NotImplementedError(
            "TODO: ML track — see implementation_plan.md §5.2, §17.2"
        )

    def expected_days_to_payment(self, features: dict[str, float]) -> float:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.2")

    def median_days_to_payment(self, features: dict[str, float]) -> float:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.2")


def train(
    X: pd.DataFrame, y: pd.Series, output_dir: Path, *, seed: int = 42
) -> TimeToPaymentModel:
    raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.2, §12.4")
