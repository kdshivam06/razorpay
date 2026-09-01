"""Model evaluation metrics (§12.4)."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


@dataclasses.dataclass(frozen=True)
class ModelMetrics:
    """Aggregate metrics for one model family (§12.4)."""

    accuracy: float
    precision: float
    recall: float
    f1: float
    au_roc: float
    extra: dict


@dataclasses.dataclass(frozen=True)
class UpliftMetrics:
    """Uplift-specific metrics (§12.4) and business value summary."""

    qini: float
    auuc: float
    incremental_recovered_paise: int
    extra: dict


class ModelEvaluator:
    """Computes standard + uplift metrics:

      classification → precision/recall/F1/confusion
      payment probability → ROC-AUC, PR-AUC, log-loss, brier, calibration
      uplift → Qini, AUUC, incremental recovery ₹
      time-to-pay → MAE, median abs err
      business → incremental ₹, recovery/contact, contacts avoided...

    Evaluates PREDICTED vs TRUE hidden ground truth (§15.3)."""

    def classification(self, y_true: pd.Series, y_pred: pd.Series) -> ModelMetrics:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.4")

    def probability(self, y_true: pd.Series, y_score: pd.Series) -> ModelMetrics:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.4")

    def uplift(self, y_true: pd.Series, uplift_scores: pd.Series) -> UpliftMetrics:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.4")
