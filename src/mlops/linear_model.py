"""Linear-regression baseline pipeline for sales prediction."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from importlib.metadata import version
from typing import cast

from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from src.contracts.mlops import ModelRunMetadata, PrimitiveValue, SalesFeatureRow
from src.mlops.encoding import FrequencyRareBucketEncoder
from src.mlops.features import (
    HIGH_CARDINALITY_FIELDS,
    LOW_CARDINALITY_FIELDS,
    MODEL_FEATURE_FIELDS,
    NUMERIC_FIELDS,
    build_feature_vector,
    categorical_vocabulary,
)


_LINEAR_MODEL_LIBRARY = "scikit-learn"


def build_pipeline(hyperparameters: dict[str, PrimitiveValue]) -> Pipeline:
    """Build the unfit sklearn pipeline for the linear-regression baseline."""
    low_cardinality_indices = [MODEL_FEATURE_FIELDS.index(name) for name in LOW_CARDINALITY_FIELDS]
    high_cardinality_indices = [MODEL_FEATURE_FIELDS.index(name) for name in HIGH_CARDINALITY_FIELDS]
    numeric_indices = [MODEL_FEATURE_FIELDS.index(name) for name in NUMERIC_FIELDS]

    preprocessing = ColumnTransformer(
        transformers=[
            (
                "one_hot",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                low_cardinality_indices,
            ),
            (
                "frequency",
                FrequencyRareBucketEncoder(),
                high_cardinality_indices,
            ),
            ("numeric", "passthrough", numeric_indices),
        ]
    )
    estimator = LinearRegression(**cast(dict[str, object], hyperparameters))
    return Pipeline(
        steps=[
            ("preprocessor", preprocessing),
            ("estimator", estimator),
        ]
    )


def build_linear_feature_matrix(rows: list[SalesFeatureRow]) -> list[list[object]]:
    """Build the stable feature matrix expected by the sklearn pipeline."""
    return [build_feature_vector(row, MODEL_FEATURE_FIELDS) for row in rows]


def fit_linear_model(
    train_rows: list[SalesFeatureRow], hyperparameters: dict[str, PrimitiveValue]
) -> tuple[Pipeline, ModelRunMetadata]:
    """Fit the baseline linear-regression pipeline."""
    pipeline = build_pipeline(hyperparameters)
    feature_matrix = build_linear_feature_matrix(train_rows)
    targets = [float(row.sales) for row in train_rows if row.sales is not None]
    started_at = time.perf_counter()
    trained_at = time.time()
    pipeline.fit(feature_matrix, targets)
    training_duration_ms = int(round((time.perf_counter() - started_at) * 1000))
    setattr(pipeline, "_mlops_categorical_vocabularies", categorical_vocabulary(train_rows))
    metadata = ModelRunMetadata(
        model_name="linear_regression",
        hyperparameters=hyperparameters,
        library_versions={_LINEAR_MODEL_LIBRARY: version(_LINEAR_MODEL_LIBRARY)},
        data_hash="",
        trained_at=time_to_datetime(trained_at),
        train_row_count=len(train_rows),
        test_row_count=0,
        split_cutoff_date=train_rows[-1].order_date,
        training_duration_ms=training_duration_ms,
    )
    return pipeline, metadata


def time_to_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


__all__ = [
    "build_linear_feature_matrix",
    "build_pipeline",
    "fit_linear_model",
]
