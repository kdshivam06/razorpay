"""Model evaluation metrics (§12.4)."""

from __future__ import annotations

import dataclasses
import math
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
        from sklearn.metrics import (
            accuracy_score,
            confusion_matrix,
            f1_score,
            precision_score,
            recall_score,
        )

        labels = sorted(set(y_true) | set(y_pred), key=str)
        return ModelMetrics(
            accuracy=float(accuracy_score(y_true, y_pred)),
            precision=float(precision_score(y_true, y_pred, average="weighted", zero_division=0)),
            recall=float(recall_score(y_true, y_pred, average="weighted", zero_division=0)),
            f1=float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
            au_roc=0.0,
            extra={
                "labels": [str(label) for label in labels],
                "confusion_matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
            },
        )

    def probability(self, y_true: pd.Series, y_score: pd.Series) -> ModelMetrics:
        from sklearn.metrics import (
            accuracy_score,
            average_precision_score,
            brier_score_loss,
            f1_score,
            log_loss,
            precision_score,
            recall_score,
            roc_auc_score,
        )

        clipped_scores = [min(1.0, max(0.0, float(score))) for score in y_score]
        y_pred = [1 if score >= 0.5 else 0 for score in clipped_scores]
        unique_labels = set(int(label) for label in y_true)
        au_roc = (
            float(roc_auc_score(y_true, clipped_scores))
            if len(unique_labels) > 1
            else 0.5
        )
        pr_auc = (
            float(average_precision_score(y_true, clipped_scores))
            if len(unique_labels) > 1
            else float(next(iter(unique_labels), 0))
        )
        return ModelMetrics(
            accuracy=float(accuracy_score(y_true, y_pred)),
            precision=float(precision_score(y_true, y_pred, zero_division=0)),
            recall=float(recall_score(y_true, y_pred, zero_division=0)),
            f1=float(f1_score(y_true, y_pred, zero_division=0)),
            au_roc=au_roc,
            extra={
                "pr_auc": pr_auc,
                "log_loss": float(log_loss(y_true, clipped_scores, labels=[0, 1])),
                "brier_score": float(brier_score_loss(y_true, clipped_scores)),
                "calibration": _calibration_bins(y_true, clipped_scores),
            },
        )

    def uplift(self, y_true: pd.Series, uplift_scores: pd.Series) -> UpliftMetrics:
        paired = sorted(
            ((float(score), int(label)) for label, score in zip(y_true, uplift_scores)),
            key=lambda item: item[0],
            reverse=True,
        )
        if not paired:
            return UpliftMetrics(
                qini=0.0,
                auuc=0.0,
                incremental_recovered_paise=0,
                extra={"qini_curve": [], "uplift_at_k": {}},
            )

        total_positive = sum(label for _, label in paired)
        qini_curve = [(0.0, 0.0)]
        gains_curve = [(0.0, 0.0)]
        cumulative_positive = 0
        population = len(paired)

        for rank, (_, label) in enumerate(paired, start=1):
            cumulative_positive += label
            expected_random = total_positive * rank / population
            fraction = rank / population
            qini_curve.append((fraction, cumulative_positive - expected_random))
            gains_curve.append((fraction, cumulative_positive / max(1, total_positive)))

        uplift_at_k = {
            str(k): _uplift_at_fraction(paired, k)
            for k in (0.1, 0.2, 0.3, 0.5)
        }
        top_30_count = max(1, math.ceil(population * 0.3))
        incremental_recovered = sum(label for _, label in paired[:top_30_count]) * 100_000

        return UpliftMetrics(
            qini=round(_area_under_curve(qini_curve), 6),
            auuc=round(_area_under_curve(gains_curve), 6),
            incremental_recovered_paise=int(incremental_recovered),
            extra={
                "qini_curve": [[round(x, 6), round(y, 6)] for x, y in qini_curve],
                "uplift_at_k": uplift_at_k,
                "top_30_percent_case_count": top_30_count,
            },
        )


def _calibration_bins(y_true: object, y_score: list[float], bins: int = 10) -> list[dict]:
    rows = []
    labels = [int(label) for label in y_true]
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [
            (label, score)
            for label, score in zip(labels, y_score)
            if lower <= score < upper or (index == bins - 1 and score == 1.0)
        ]
        if not members:
            continue
        observed = sum(label for label, _ in members) / len(members)
        predicted = sum(score for _, score in members) / len(members)
        rows.append(
            {
                "bin": index,
                "count": len(members),
                "predicted": round(predicted, 6),
                "observed": round(observed, 6),
            }
        )
    return rows


def _uplift_at_fraction(paired: list[tuple[float, int]], fraction: float) -> float:
    top_count = max(1, math.ceil(len(paired) * fraction))
    overall_rate = sum(label for _, label in paired) / len(paired)
    top_rate = sum(label for _, label in paired[:top_count]) / top_count
    return round(top_rate - overall_rate, 6)


def _area_under_curve(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        area += (right_x - left_x) * (left_y + right_y) / 2
    return area
