"""Unit tests for MLOps inference fallback detection."""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

from src.contracts.mlops import PredictionInput
from src.mlops.inference import predict_sales
from src.mlops.registry import ArtifactRegistry
from tests.unit._mlops_support import sample_training_rows, registry_test_root
from src.mlops.catboost_model import fit_catboost_model
from src.contracts.mlops import EvaluationMetrics


def test_unseen_category_sets_used_fallback_encoding() -> None:
    root = registry_test_root('mlops-unit-inference')
    if root.exists():
        shutil.rmtree(root)
    registry = ArtifactRegistry(root=root)
    rows = sample_training_rows()
    model, metadata = fit_catboost_model(
        rows[:15],
        {
            "iterations": 5,
            "depth": 2,
            "learning_rate": 0.1,
            "loss_function": "RMSE",
            "random_seed": 42,
            "allow_writing_files": False,
        },
    )
    metadata = metadata.model_copy(update={"data_hash": "hash-3", "test_row_count": 5, "split_cutoff_date": rows[15].order_date})
    metrics = EvaluationMetrics(rmse=0.9, mae=0.7, r2=0.6, test_row_count=5, split_cutoff_date=rows[15].order_date)
    entry = registry.persist_run("catboost", model, metadata, metrics)
    registry.promote(entry.run_id, "dev")

    result = predict_sales(
        registry,
        "dev",
        PredictionInput(
            order_date=rows[-1].order_date,
            ship_mode="Second Class",
            segment="Consumer",
            region="West",
            market="US",
            product_id="NEW-SKU-999",
            sub_category="Accessories",
            category="Technology",
            quantity=2,
            discount=Decimal("0.0"),
        ),
    )
    assert result.used_fallback_encoding is True
    shutil.rmtree(root)
