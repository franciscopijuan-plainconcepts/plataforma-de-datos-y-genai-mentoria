"""End-to-end orchestration for sales-model training."""

from __future__ import annotations

from src.contracts.data_access import TableDef
from src.contracts.mlops import ArtifactRegistryEntry, PrimitiveValue
from src.data_access.interfaces import QueryProvider
from src.mlops.catboost_model import fit_catboost_model
from src.mlops.dataset import extract_feature_set
from src.mlops.evaluation import evaluate
from src.mlops.linear_model import fit_linear_model
from src.mlops.registry import ArtifactRegistry
from src.mlops.split import chronological_split


_DEFAULT_LINEAR_HYPERPARAMETERS: dict[str, PrimitiveValue] = {
    "fit_intercept": True,
}

_DEFAULT_CATBOOST_HYPERPARAMETERS: dict[str, PrimitiveValue] = {
    "iterations": 500,
    "depth": 6,
    "learning_rate": 0.05,
    "loss_function": "RMSE",
    "random_seed": 42,
    "allow_writing_files": False,
}


def train_sales_models(
    query_provider: QueryProvider,
    orders_table_def: TableDef,
    registry: ArtifactRegistry,
    linear_hyperparameters: dict[str, PrimitiveValue] | None = None,
    catboost_hyperparameters: dict[str, PrimitiveValue] | None = None,
    test_fraction: float = 0.2,
    min_test_rows: int = 500,
) -> tuple[ArtifactRegistryEntry, ArtifactRegistryEntry]:
    """Extract, split, train, evaluate, and persist both sales models."""
    feature_set = extract_feature_set(query_provider, orders_table_def)
    train_rows, test_rows = chronological_split(
        feature_set.rows,
        test_fraction=test_fraction,
        min_test_rows=min_test_rows,
    )
    split_cutoff_date = test_rows[0].order_date

    fit_linear_hyperparameters = {
        **_DEFAULT_LINEAR_HYPERPARAMETERS,
        **(linear_hyperparameters or {}),
    }
    fit_catboost_hyperparameters = {
        **_DEFAULT_CATBOOST_HYPERPARAMETERS,
        **(catboost_hyperparameters or {}),
    }
    tracked_linear_hyperparameters = {
        **fit_linear_hyperparameters,
        "test_fraction": test_fraction,
        "min_test_rows": min_test_rows,
    }
    tracked_catboost_hyperparameters = {
        **fit_catboost_hyperparameters,
        "test_fraction": test_fraction,
        "min_test_rows": min_test_rows,
    }

    linear_model, linear_metadata = fit_linear_model(
        train_rows, fit_linear_hyperparameters
    )
    linear_metadata = linear_metadata.model_copy(
        update={
            "hyperparameters": tracked_linear_hyperparameters,
            "data_hash": feature_set.data_hash,
            "test_row_count": len(test_rows),
            "split_cutoff_date": split_cutoff_date,
        }
    )
    linear_metrics = evaluate(linear_model, test_rows, split_cutoff_date)
    persisted_linear = registry.persist_run(
        "linear_regression", linear_model, linear_metadata, linear_metrics
    )

    catboost_model, catboost_metadata = fit_catboost_model(
        train_rows, fit_catboost_hyperparameters
    )
    catboost_metadata = catboost_metadata.model_copy(
        update={
            "hyperparameters": tracked_catboost_hyperparameters,
            "data_hash": feature_set.data_hash,
            "test_row_count": len(test_rows),
            "split_cutoff_date": split_cutoff_date,
        }
    )
    catboost_metrics = evaluate(catboost_model, test_rows, split_cutoff_date)
    persisted_catboost = registry.persist_run(
        "catboost", catboost_model, catboost_metadata, catboost_metrics
    )
    return persisted_linear, persisted_catboost


__all__ = ["train_sales_models"]
