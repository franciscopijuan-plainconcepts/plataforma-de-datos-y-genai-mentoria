"""Unit tests for the filesystem-backed MLOps registry."""

from __future__ import annotations

import json
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from catboost import CatBoostRegressor
from sklearn.pipeline import Pipeline

from src.contracts.mlops import EvaluationMetrics, ModelRunMetadata
from src.mlops.catboost_model import fit_catboost_model
from src.mlops.linear_model import fit_linear_model
from src.mlops.registry import ArtifactRegistry, PromotionGateError, UnknownRunIdError
from tests.unit._mlops_support import sample_training_rows, registry_test_root


@pytest.fixture()
def registry_root() -> Generator[Path, None, None]:
    root = registry_test_root('mlops-unit-registry')
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    if root.exists():
        shutil.rmtree(root)


@pytest.fixture()
def registry(registry_root: Path) -> ArtifactRegistry:
    return ArtifactRegistry(root=registry_root)


@pytest.fixture()
def fitted_linear_run() -> tuple[Pipeline, ModelRunMetadata, EvaluationMetrics]:
    rows = sample_training_rows()
    model, metadata = fit_linear_model(rows[:15], {"fit_intercept": True})
    metadata = metadata.model_copy(update={"data_hash": "hash-1", "test_row_count": 5, "split_cutoff_date": rows[15].order_date})
    metrics = EvaluationMetrics(rmse=1.0, mae=0.8, r2=0.5, test_row_count=5, split_cutoff_date=rows[15].order_date)
    return model, metadata, metrics


@pytest.fixture()
def fitted_catboost_run() -> tuple[CatBoostRegressor, ModelRunMetadata, EvaluationMetrics]:
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
    metadata = metadata.model_copy(update={"data_hash": "hash-2", "test_row_count": 5, "split_cutoff_date": rows[15].order_date})
    metrics = EvaluationMetrics(rmse=0.9, mae=0.7, r2=0.6, test_row_count=5, split_cutoff_date=rows[15].order_date)
    return model, metadata, metrics


def test_persist_run_writes_expected_files_and_unique_run_ids(
    registry: ArtifactRegistry,
    registry_root: Path,
    fitted_linear_run: tuple[Pipeline, ModelRunMetadata, EvaluationMetrics],
) -> None:
    model, metadata, metrics = fitted_linear_run
    first = registry.persist_run("linear_regression", model, metadata, metrics)
    second = registry.persist_run("linear_regression", model, metadata, metrics)
    assert first.run_id != second.run_id

    run_dir = registry_root / "models" / "linear_regression" / first.run_id
    assert (run_dir / "params.json").exists()
    assert (run_dir / "metrics.json").exists()
    assert (run_dir / "data_hash.txt").exists()
    assert (run_dir / "model.joblib").exists()


def test_persist_run_failure_leaves_previous_registry_json_untouched(
    registry: ArtifactRegistry,
    registry_root: Path,
    fitted_linear_run: tuple[Pipeline, ModelRunMetadata, EvaluationMetrics],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, metadata, metrics = fitted_linear_run
    first = registry.persist_run("linear_regression", model, metadata, metrics)
    before = (registry_root / "registry.json").read_text(encoding="utf-8")

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(registry, "_serialize_model", _boom)
    with pytest.raises(RuntimeError):
        registry.persist_run("linear_regression", model, metadata, metrics)
    after = (registry_root / "registry.json").read_text(encoding="utf-8")
    assert after == before
    assert registry.list_runs()[0].run_id == first.run_id


def test_list_runs_reads_only_registry_json(
    registry: ArtifactRegistry,
    registry_root: Path,
    fitted_linear_run: tuple[Pipeline, ModelRunMetadata, EvaluationMetrics],
) -> None:
    model, metadata, metrics = fitted_linear_run
    persisted = registry.persist_run("linear_regression", model, metadata, metrics)
    run_dir = registry_root / "models" / "linear_regression" / persisted.run_id
    (run_dir / "model.joblib").unlink()
    runs = registry.list_runs("linear_regression")
    assert [entry.run_id for entry in runs] == [persisted.run_id]


def test_promote_enforces_staging_gate_and_preserves_history(
    registry: ArtifactRegistry,
    fitted_linear_run: tuple[Pipeline, ModelRunMetadata, EvaluationMetrics],
    fitted_catboost_run: tuple[CatBoostRegressor, ModelRunMetadata, EvaluationMetrics],
) -> None:
    linear_model, linear_metadata, linear_metrics = fitted_linear_run
    cat_model, cat_metadata, cat_metrics = fitted_catboost_run
    first = registry.persist_run("linear_regression", linear_model, linear_metadata, linear_metrics)
    second = registry.persist_run("catboost", cat_model, cat_metadata, cat_metrics)

    registry.promote(first.run_id, "dev")
    registry.promote(first.run_id, "staging")
    with pytest.raises(PromotionGateError):
        registry.promote(second.run_id, "prod")

    bypass = registry.promote(second.run_id, "prod", force_bypass_staging_gate=True)
    assert bypass.bypassed_staging_gate is True

    registry.promote(first.run_id, "prod")
    doc = json.loads((registry_test_root('mlops-unit-registry') / 'registry.json').read_text(encoding='utf-8'))
    assert len(doc["promotion_history"]) == 4
    assert registry.resolve_active_run("prod") is not None


def test_promote_unknown_run_lists_available_run_ids(
    registry: ArtifactRegistry,
    fitted_linear_run: tuple[Pipeline, ModelRunMetadata, EvaluationMetrics],
) -> None:
    model, metadata, metrics = fitted_linear_run
    persisted = registry.persist_run("linear_regression", model, metadata, metrics)
    with pytest.raises(UnknownRunIdError, match=persisted.run_id):
        registry.promote("does-not-exist", "dev")
