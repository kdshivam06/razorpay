"""Payment propensity model — P(payment within a horizon) (§5.1)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


@dataclasses.dataclass(frozen=True)
class PropensityCurve:
    """Survival-style horizon probabilities from §5.1 (all 0..1)."""

    p_pay_within_1h: float
    p_pay_within_6h: float
    p_pay_within_24h: float
    p_pay_within_3d: float
    p_pay_within_7d: float


class PaymentPropensityModel:
    """Predicts the probability that a case pays within each horizon."""

    def __init__(self, artifact_path: Path | None = None) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.1, §17.2")

    def propensity_curve(self, features: dict[str, float]) -> PropensityCurve:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.1")

    def probability_within(self, features: dict[str, float], hours: int) -> float:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.1")


def train(
    X: pd.DataFrame, y: pd.DataFrame, output_dir: Path, *, seed: int = 42
) -> PaymentPropensityModel:
    raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.1, §12.4")