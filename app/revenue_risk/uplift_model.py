"""Incremental uplift model — per-action uplift over NO_ACTION (§5.3)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from app.contracts import Action

if TYPE_CHECKING:
    import pandas as pd


@dataclasses.dataclass(frozen=True)
class UpliftEstimates:
    """P(pay | each action) and the uplift vs NO_ACTION (§5.3, §6.2)."""

    baseline_natural_probability: float
    per_action_probability: dict[Action, float]
    per_action_uplift: dict[Action, float]


class IncrementalUpliftModel:
    """The ★ biggest AI differentiator (§5.3): heterogeneous treatment effects.

    Avoids spending intervention cost on customers who would have paid anyway.
    """

    def __init__(self, artifact_path: Path | None = None) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.3, §17.2")

    def uplift_for(self, features: dict[str, float], action: Action) -> float:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.3")

    def estimates(self, features: dict[str, float]) -> UpliftEstimates:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.3")

    def best_action(self, features: dict[str, float]) -> Action:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.3")


def train(
    X: pd.DataFrame,
    treatment: pd.Series,
    outcome: pd.Series,
    output_dir: Path,
    *,
    seed: int = 42,
) -> IncrementalUpliftModel:
    raise NotImplementedError("TODO: ML track — see implementation_plan.md §5.3, §12.4")