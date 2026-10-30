"""v3.3 row-level backtest: holdout scoring, persistence, idempotency, accuracy view."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlglot
from sqlglot import exp

from src.contracts.data_access import BacktestRow, LoadResult, OrderRow, Row, TableDef
from src.contracts.mlops import EvaluationMetrics, FeatureSet, ModelRunMetadata, SalesFeatureRow
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import QueryRow
from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver
from src.mlops.backtest import BacktestRepository, run_backtest, select_holdout
from src.mlops.catboost_model import fit_catboost_model
from src.mlops.predictions_store import (
    BACKTEST_TABLE_NAME,
    FORECAST_ACCURACY_SQL,
    FORECAST_ACCURACY_VIEW_NAME,
    backtest_table_def,
    ensure_backtest_objects,
    forecast_accuracy_view_def,
)
from src.mlops.registry import ArtifactRegistry, NoActiveModelError
from tests.semantic_fixtures import _build_test_table_defs
from tests.unit._mlops_support import registry_test_root, sample_training_rows


class _FakeRepo:
    """In-memory SchemaProvider + DataProvider + QueryProvider."""

    def __init__(self) -> None:
        self.tables: list[str] = []
        self.views: dict[str, str] = {}
        self.loaded: list[BacktestRow] = []

    def create_table(self, table_def: TableDef) -> None:
        self.tables.append(table_def.name)

    def drop_table(self, name: str) -> None:  # pragma: no cover - protocol filler
        raise AssertionError("backtest never drops tables")

    def table_exists(self, name: str) -> bool:  # pragma: no cover - protocol filler
        return name in self.tables

    def create_or_replace_view(self, name: str, select_sql: str) -> None:
        self.views[name] = select_sql

    def load_rows(self, table_name: str, rows: list[Row]) -> LoadResult:
        assert table_name == BACKTEST_TABLE_NAME
        self.loaded.extend(row for row in rows if isinstance(row, BacktestRow))
        return LoadResult(table_name=table_name, rows_loaded=len(rows))

    def find_orders_by_region(self, region: str) -> list[OrderRow]:  # pragma: no cover
        return []

    def count_rows(self, table_name: str) -> int:  # pragma: no cover
        return len(self.loaded)

    def list_tables(self) -> list[str]:  # pragma: no cover
        return self.tables

    def execute_readonly_query(self, sql: str, table_def: TableDef) -> list[QueryRow]:
        # Only the idempotency probe reaches the repo (extraction is patched).
        assert table_def.name == BACKTEST_TABLE_NAME
        return [QueryRow(data={"Run ID": row.run_id}) for row in self.loaded if f"'{row.run_id}'" in sql]


@pytest.fixture()
def promoted() -> Iterator[tuple[ArtifactRegistry, datetime]]:
    root = registry_test_root("mlops-unit-backtest")
    if root.exists():
        shutil.rmtree(root)
    registry = ArtifactRegistry(root=root)
    rows = sample_training_rows()
    cutoff = rows[15].order_date
    model, metadata = fit_catboost_model(rows[:15], {
        "iterations": 5, "depth": 2, "learning_rate": 0.1, "loss_function": "RMSE",
        "random_seed": 42, "allow_writing_files": False,
    })
    # Tracked like `train_sales_models` does, so the backtest replays the split.
    metadata = metadata.model_copy(update={
        "data_hash": "hash-bt", "test_row_count": 5, "split_cutoff_date": cutoff,
        "hyperparameters": {**metadata.hyperparameters, "test_fraction": 0.25, "min_test_rows": 5},
    })
    metrics = EvaluationMetrics(rmse=1.0, mae=1.0, r2=0.5, test_row_count=5, split_cutoff_date=cutoff)
    entry = registry.persist_run("catboost", model, metadata, metrics)
    registry.promote(entry.run_id, "dev")
    yield registry, cutoff
    shutil.rmtree(root)


def _feature_set(rows: list[SalesFeatureRow] | None = None, data_hash: str = "hash-bt") -> FeatureSet:
    rows = rows if rows is not None else sample_training_rows()
    return FeatureSet(rows=rows, data_hash=data_hash, extracted_at=datetime.now(timezone.utc),
                      source_table="Orders", row_count=len(rows))


def _metadata(cutoff: datetime, **hyperparameters: float | int) -> ModelRunMetadata:
    return ModelRunMetadata(
        model_name="catboost", hyperparameters=dict(hyperparameters), library_versions={},
        data_hash="hash-bt", trained_at=datetime.now(timezone.utc), train_row_count=15,
        test_row_count=5, split_cutoff_date=cutoff, training_duration_ms=0,
    )


def _rows_sharing_the_cutoff_date() -> list[SalesFeatureRow]:
    """Rows 13..16 share one date: the index split puts 13-14 in train, 15-16 in test."""
    rows = sample_training_rows()
    shared = rows[13].order_date
    return [row.model_copy(update={"order_date": shared}) if 13 <= index <= 16 else row
            for index, row in enumerate(rows)]


def _orders_def() -> TableDef:
    return _build_test_table_defs()[0]


def test_select_holdout_replays_the_exact_split_on_a_shared_cutoff_date() -> None:
    rows = _rows_sharing_the_cutoff_date()
    metadata = _metadata(rows[15].order_date, test_fraction=0.25, min_test_rows=5)
    holdout = select_holdout(_feature_set(rows), metadata)
    # Rows 13-14 trained the model although they share the cutoff date.
    assert holdout == rows[15:]
    assert sum(row.order_date >= metadata.split_cutoff_date for row in rows) == 7


@pytest.mark.parametrize("data_hash, hyperparameters", [
    ("other-data", {"test_fraction": 0.25, "min_test_rows": 5}),
    ("hash-bt", {}),
    ("hash-bt", {"test_fraction": 0.5, "min_test_rows": 5}),
])
def test_select_holdout_falls_back_to_strictly_after_the_cutoff(
    data_hash: str, hyperparameters: dict[str, float | int],
) -> None:
    rows = _rows_sharing_the_cutoff_date()
    cutoff = rows[15].order_date.replace(tzinfo=None)
    holdout = select_holdout(_feature_set(rows, data_hash), _metadata(cutoff, **hyperparameters))
    assert holdout == rows[17:]


def test_select_holdout_drops_rows_without_target() -> None:
    rows = sample_training_rows()
    rows[19] = rows[19].model_copy(update={"sales": None})
    metadata = _metadata(rows[15].order_date, test_fraction=0.25, min_test_rows=5)
    assert select_holdout(_feature_set(rows), metadata) == rows[15:19]


def test_run_backtest_scores_holdout_once_and_persists(promoted: tuple[ArtifactRegistry, datetime]) -> None:
    registry, cutoff = promoted
    repo = _FakeRepo()
    assert isinstance(repo, BacktestRepository)
    with patch("src.mlops.backtest.extract_feature_set", return_value=_feature_set()), \
            patch.object(registry, "load_model", wraps=registry.load_model) as load_model:
        written = run_backtest(repo, _orders_def(), registry, environment="dev")

    assert written == 5 and len(repo.loaded) == 5
    load_model.assert_called_once()
    assert repo.tables == [BACKTEST_TABLE_NAME]
    assert repo.views == {FORECAST_ACCURACY_VIEW_NAME: FORECAST_ACCURACY_SQL}
    expected_actuals = [row.sales for row in sample_training_rows()[15:]]
    assert [row.actual_sales for row in repo.loaded] == expected_actuals
    first = repo.loaded[0]
    assert first.backtest_id == f"{first.run_id}:0"
    assert first.model_name == "catboost" and first.region == "West" and first.category == "Technology"
    assert all(row.order_date >= cutoff for row in repo.loaded)
    assert all(row.predicted_sales == row.predicted_sales.quantize(Decimal("0.01")) for row in repo.loaded)


def test_run_backtest_is_idempotent_per_run(promoted: tuple[ArtifactRegistry, datetime]) -> None:
    registry, _ = promoted
    repo = _FakeRepo()
    with patch("src.mlops.backtest.extract_feature_set", return_value=_feature_set()) as extract:
        assert run_backtest(repo, _orders_def(), registry, environment="dev") == 5
        assert run_backtest(repo, _orders_def(), registry, environment="dev") == 0
    assert extract.call_count == 1
    assert len(repo.loaded) == 5


def test_ensure_backtest_objects_needs_no_ml_imports() -> None:
    """`bootstrap` creates the view unconditionally, before any ML import."""
    repo = _FakeRepo()
    ensure_backtest_objects(repo)
    ensure_backtest_objects(repo)
    assert repo.tables == [BACKTEST_TABLE_NAME] * 2
    assert repo.views == {FORECAST_ACCURACY_VIEW_NAME: FORECAST_ACCURACY_SQL}
    probe = ("import sys, src.mlops.predictions_store; "
             "sys.exit(any(m in sys.modules for m in ('catboost', 'sklearn')))")
    assert subprocess.run([sys.executable, "-c", probe], check=False).returncode == 0


def test_run_backtest_requires_active_model(promoted: tuple[ArtifactRegistry, datetime]) -> None:
    registry, _ = promoted
    with pytest.raises(NoActiveModelError):
        run_backtest(_FakeRepo(), _orders_def(), registry, environment="prod")


def test_accuracy_view_sql_matches_its_table_def() -> None:
    tree = sqlglot.parse_one(FORECAST_ACCURACY_SQL, read="postgres")
    assert isinstance(tree, exp.Select)
    assert tree.named_selects == [column.name for column in forecast_accuracy_view_def().columns]
    sources = {table.name for table in tree.find_all(exp.Table)}
    assert sources == {BACKTEST_TABLE_NAME}
    backtest_columns = {column.name for column in backtest_table_def().columns}
    assert {column.name for column in tree.find_all(exp.Column)} <= backtest_columns


def test_accuracy_view_is_region_filtered() -> None:
    viewer = SemanticViewer(viewer_id="caribbean", regions=["Caribbean"])
    sql = 'SELECT SUM("Abs Error") / NULLIF(SUM("Actual Sales"), 0) FROM "Forecast Accuracy"'
    result = SemanticQueryResolver().apply_rls(
        sql, viewer, _orders_def(), extra_tables={"forecast accuracy": forecast_accuracy_view_def()},
    )
    assert 'FROM (SELECT * FROM "Forecast Accuracy" WHERE "Region" IN (\'Caribbean\'))' in result
