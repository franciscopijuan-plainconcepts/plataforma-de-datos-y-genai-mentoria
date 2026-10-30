"""Row-level backtest of the active sales model (v3.3, base of M5 drift).

Forecasts in `Predictions` target future months that the static Global
Superstore data will never reach, so "predicted vs actual" is measured where
actuals do exist: the model's own chronological holdout. Training fits only
rows before `split_cutoff_date` (`src/mlops/training.py`), so every Orders
line on/after that date is out-of-sample for the run. Each of those lines is
scored once with the run's model and persisted in `Forecast Backtest`
(actual vs predicted `Sales`); the `Forecast Accuracy` view aggregates them to
month x Region x Category, which is the grain the semantic metrics
(`forecast_wape`, `forecast_bias`, ...) and the dashboard read.

Scoring reuses the shared feature derivation and `predict_feature_rows`
(no parallel prediction logic) but loads the model once for the whole batch
instead of calling `predict_sales()` per line. Idempotent per `Run ID`: a new
run appends its own rows, which keeps a per-model accuracy history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol, runtime_checkable

from src.contracts.data_access import BacktestRow, TableDef
from src.contracts.mlops import ArtifactRegistryEntry, EnvironmentName, SalesFeatureRow
from src.data_access.interfaces import DataProvider, QueryProvider, SchemaProvider
from src.data_access.sql_validator import validate_sql
from src.mlops.dataset import extract_feature_set
from src.mlops.inference import predict_feature_rows
from src.mlops.predictions_store import (
    BACKTEST_TABLE_NAME,
    FORECAST_ACCURACY_SQL,
    FORECAST_ACCURACY_VIEW_NAME,
    backtest_table_def,
    forecast_accuracy_view_def,
    forecast_semantic_tables,
)
from src.mlops.registry import ArtifactRegistry, NoActiveModelError


@runtime_checkable
class BacktestRepository(SchemaProvider, DataProvider, QueryProvider, Protocol):
    """A repository able to host the backtest table/view and read Orders."""


def ensure_backtest_objects(schema_provider: SchemaProvider) -> None:
    """Create the backtest table and (re)define the accuracy view (idempotent)."""
    schema_provider.create_table(backtest_table_def())
    schema_provider.create_or_replace_view(FORECAST_ACCURACY_VIEW_NAME, FORECAST_ACCURACY_SQL)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def select_holdout(rows: list[SalesFeatureRow], cutoff: datetime) -> list[SalesFeatureRow]:
    """Out-of-sample rows for a run: on/after its split cutoff, with a known target."""
    boundary = _utc(cutoff)
    return [row for row in rows if row.sales is not None and _utc(row.order_date) >= boundary]


def build_backtest_rows(
    entry: ArtifactRegistryEntry,
    holdout: list[SalesFeatureRow],
    predictions: list[float],
    *,
    scored_at: datetime,
) -> list[BacktestRow]:
    if len(holdout) != len(predictions):
        raise ValueError("Each holdout row needs exactly one prediction")
    return [
        BacktestRow(
            backtest_id=f"{entry.run_id}:{index}",
            run_id=entry.run_id,
            model_name=entry.model_name,
            trained_at=entry.trained_at,
            order_date=row.order_date,
            region=row.region,
            market=row.market,
            category=row.category,
            sub_category=row.sub_category,
            actual_sales=row.sales if row.sales is not None else Decimal("0"),
            predicted_sales=Decimal(str(round(predicted, 2))),
            scored_at=scored_at,
        )
        for index, (row, predicted) in enumerate(zip(holdout, predictions))
    ]


def _has_backtest(query_provider: QueryProvider, run_id: str) -> bool:
    literal = run_id.replace("'", "''")
    sql = f'SELECT "Run ID" FROM "Forecast Backtest" WHERE "Run ID" = \'{literal}\' LIMIT 1'
    table_def = backtest_table_def()
    validation = validate_sql(sql, table_def)
    if not validation.accepted:
        raise ValueError(f"Backtest idempotency SQL failed validation: {validation.reason}")
    return bool(query_provider.execute_readonly_query(sql, table_def))


def run_backtest(
    repo: BacktestRepository,
    orders_table_def: TableDef,
    registry: ArtifactRegistry,
    environment: EnvironmentName = "prod",
) -> int:
    """Score the holdout of the run active in `environment` and persist it.

    Returns the number of rows written (`0` when this run was already
    backtested or has no holdout rows).
    """
    ensure_backtest_objects(repo)
    entry = registry.resolve_active_run(environment)
    if entry is None:
        raise NoActiveModelError(f"No active model promoted in environment {environment!r}.")
    if _has_backtest(repo, entry.run_id):
        return 0

    feature_set = extract_feature_set(repo, orders_table_def)
    holdout = select_holdout(feature_set.rows, entry.metrics.split_cutoff_date)
    if not holdout:
        return 0
    model = registry.load_model(entry)
    predictions = predict_feature_rows(model, entry.model_name, holdout)
    rows = build_backtest_rows(entry, holdout, predictions, scored_at=datetime.now(timezone.utc))
    repo.load_rows(BACKTEST_TABLE_NAME, list(rows))
    return len(rows)


__all__ = [
    "BACKTEST_TABLE_NAME",
    "FORECAST_ACCURACY_VIEW_NAME",
    "BacktestRepository",
    "backtest_table_def",
    "build_backtest_rows",
    "ensure_backtest_objects",
    "forecast_accuracy_view_def",
    "forecast_semantic_tables",
    "run_backtest",
    "select_holdout",
]
