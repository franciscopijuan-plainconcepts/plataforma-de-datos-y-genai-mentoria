"""Inference flow for predict-sales CLI."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from catboost import CatBoostRegressor
from sklearn.pipeline import Pipeline

from src.contracts.mlops import EnvironmentName, PredictionInput, PredictionResult, SalesFeatureRow
from src.mlops.catboost_model import build_catboost_feature_matrix
from src.mlops.features import CATEGORICAL_FIELDS, derive_prediction_row
from src.mlops.linear_model import build_linear_feature_matrix
from src.mlops.predictions_store import PredictionsRepository, persist_prediction
from src.mlops.registry import ArtifactRegistry, NoActiveModelError


_LOG_PATH = Path(".artifacts/mlops/predict_sales.log")


def predict_sales(
    registry: ArtifactRegistry,
    environment: EnvironmentName,
    prediction_input: PredictionInput,
    predictions_repository: PredictionsRepository | None = None,
) -> PredictionResult:
    """Load the promoted model for an environment and predict sales.

    When `predictions_repository` is provided (e.g. a `PostgresRepository`),
    the prediction is also persisted as a historic row in the `Predictions`
    SQL table (predicted sales, the date/hour of the prediction, and every
    input parameter used) — in addition to the JSONL append-log below.
    """
    started_at = time.perf_counter()
    active_entry = registry.resolve_active_run(environment)
    if active_entry is None:
        raise NoActiveModelError(f"No active model promoted in environment {environment!r}.")

    model = registry.load_model(active_entry)
    feature_row = derive_prediction_row(prediction_input)
    used_fallback_encoding = _check_unseen_categories(feature_row, model)
    predicted_value = _predict(model, active_entry.model_name, feature_row)
    latency_ms = int(round((time.perf_counter() - started_at) * 1000))
    result = PredictionResult(
        predicted_sales=Decimal(str(round(predicted_value, 2))),
        run_id=active_entry.run_id,
        model_name=active_entry.model_name,
        environment=environment,
        used_fallback_encoding=used_fallback_encoding,
        latency_ms=latency_ms,
    )
    _log_prediction_call(prediction_input, result)
    if predictions_repository is not None:
        persist_prediction(predictions_repository, prediction_input, result)
    return result


def _predict(
    model: Pipeline | CatBoostRegressor,
    model_name: str,
    feature_row: SalesFeatureRow,
) -> float:
    if model_name == "linear_regression":
        prediction = model.predict(build_linear_feature_matrix([feature_row]))
    else:
        prediction = model.predict(build_catboost_feature_matrix([feature_row]))
    return float(prediction[0])


def _check_unseen_categories(
    feature_row: SalesFeatureRow, model: Pipeline | CatBoostRegressor
) -> bool:
    vocabularies = getattr(model, "_mlops_categorical_vocabularies", {})
    if not isinstance(vocabularies, dict):
        return False
    for field_name in CATEGORICAL_FIELDS:
        seen_values = vocabularies.get(field_name, [])
        if str(getattr(feature_row, field_name)) not in seen_values:
            return True
    return False


def _log_prediction_call(
    prediction_input: PredictionInput,
    result: PredictionResult,
) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "prediction_input": prediction_input.model_dump(mode="json"),
        "prediction_result": result.model_dump(mode="json"),
    }
    with _LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


__all__ = ["predict_sales"]
