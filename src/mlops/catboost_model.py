"""CatBoost training utilities for sales prediction."""

from __future__ import annotations

import time
from importlib.metadata import version

from catboost import CatBoostRegressor

from src.contracts.mlops import ModelRunMetadata, PrimitiveValue, SalesFeatureRow
from src.mlops.features import (
    CATBOOST_CATEGORICAL_FIELDS,
    CATBOOST_FEATURE_FIELDS,
    build_feature_vector,
    categorical_vocabulary,
)
from src.mlops.linear_model import time_to_datetime


_CATBOOST_LIBRARY = "catboost"


def build_catboost_feature_matrix(rows: list[SalesFeatureRow]) -> list[list[object]]:
    """Build the stable feature matrix used by CatBoost."""
    return [build_feature_vector(row, CATBOOST_FEATURE_FIELDS) for row in rows]


def catboost_categorical_feature_indices() -> list[int]:
    """Return the categorical column indices for the CatBoost matrix."""
    return [CATBOOST_FEATURE_FIELDS.index(name) for name in CATBOOST_CATEGORICAL_FIELDS]


def fit_catboost_model(
    train_rows: list[SalesFeatureRow], hyperparameters: dict[str, PrimitiveValue]
) -> tuple[CatBoostRegressor, ModelRunMetadata]:
    """Fit the CatBoost regressor with native categorical features."""
    feature_matrix = build_catboost_feature_matrix(train_rows)
    targets = [float(row.sales) for row in train_rows if row.sales is not None]
    model = CatBoostRegressor(**hyperparameters)
    started_at = time.perf_counter()
    trained_at = time.time()
    model.fit(
        feature_matrix,
        targets,
        cat_features=catboost_categorical_feature_indices(),
        verbose=False,
    )
    training_duration_ms = int(round((time.perf_counter() - started_at) * 1000))
    setattr(model, "_mlops_categorical_vocabularies", categorical_vocabulary(train_rows))
    metadata = ModelRunMetadata(
        model_name="catboost",
        hyperparameters=hyperparameters,
        library_versions={_CATBOOST_LIBRARY: version(_CATBOOST_LIBRARY)},
        data_hash="",
        trained_at=time_to_datetime(trained_at),
        train_row_count=len(train_rows),
        test_row_count=0,
        split_cutoff_date=train_rows[-1].order_date,
        training_duration_ms=training_duration_ms,
    )
    return model, metadata


__all__ = [
    "build_catboost_feature_matrix",
    "catboost_categorical_feature_indices",
    "fit_catboost_model",
]
