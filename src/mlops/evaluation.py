"""Shared model evaluation for sales-prediction runs."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from catboost import CatBoostRegressor
from sklearn.metrics import mean_absolute_error, root_mean_squared_error, r2_score
from sklearn.pipeline import Pipeline

from src.contracts.mlops import EvaluationMetrics, SalesFeatureRow
from src.mlops.catboost_model import build_catboost_feature_matrix
from src.mlops.linear_model import build_linear_feature_matrix


class SupportsPredict(Protocol):
    def predict(self, X: object) -> object:
        ...


def evaluate(
    model: Pipeline | CatBoostRegressor,
    test_rows: list[SalesFeatureRow],
    split_cutoff_date: datetime,
) -> EvaluationMetrics:
    """Evaluate a fitted model on the shared chronological test set."""
    predictions = _predict(model, test_rows)
    actuals = [float(row.sales) for row in test_rows if row.sales is not None]
    return EvaluationMetrics(
        rmse=float(root_mean_squared_error(actuals, predictions)),
        mae=float(mean_absolute_error(actuals, predictions)),
        r2=float(r2_score(actuals, predictions)),
        test_row_count=len(test_rows),
        split_cutoff_date=split_cutoff_date,
    )


def _predict(
    model: Pipeline | CatBoostRegressor | SupportsPredict,
    rows: list[SalesFeatureRow],
) -> list[float]:
    if isinstance(model, Pipeline):
        raw_predictions = model.predict(build_linear_feature_matrix(rows))
    else:
        raw_predictions = model.predict(build_catboost_feature_matrix(rows))
    return [float(value) for value in raw_predictions]


__all__ = ["evaluate"]
