"""ML root-cause classifier — LightGBM/XGBoost wrapper (§2.4, §5, §9.3)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.contracts import RootCause

if TYPE_CHECKING:
    import pandas as pd


class ClassificationModel:
    """Wraps an XGBoost/LightGBM classifier producing a RootCause distribution.

    The ML layer sits BELOW the deterministic rules in the fallback hierarchy
    (§2.4): its output is used only when rules do not apply with high
    confidence.
    """

    def __init__(self, artifact_path: Path | None = None) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §2.4")

    def predict_proba(self, features: dict[str, Any]) -> dict[RootCause, float]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.1, §12.4")

    def predict(self, features: dict[str, Any]) -> RootCause:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.1")

    @property
    def model_version(self) -> str:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.6")


def train(
    X: pd.DataFrame,
    y: pd.Series,
    output_dir: Path,
    *,
    booster: str = "lightgbm",
    seed: int = 42,
) -> ClassificationModel:
    raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.1, §17.2")