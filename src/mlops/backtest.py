"""Row-level backtest of the active sales model (v3.3, base of M5 drift).

Forecasts in `Predictions` target future months that the static Global
Superstore data will never reach, so "predicted vs actual" is measured where
actuals do exist: the model's own chronological holdout.
`chronological_split` (`src/mlops/split.py`) cuts the date-sorted rows by
INDEX, so lines sharing `split_cutoff_date` can sit on both sides of the
split; "on/after the cutoff" would leak training rows. The backtest therefore
replays the run's exact split (same `data_hash`, tracked `test_fraction` /
`min_test_rows`) and, when it cannot, keeps only lines strictly after the
cutoff. Each holdout line is scored once with the run's model and persisted
in `Forecast Backtest`
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
from src.contracts.mlops import (
    ArtifactRegistryEntry,
    EnvironmentName,
    FeatureSet,
    ModelRunMetadata,
    SalesFeatureRow,
)
from src.data_access.interfaces import DataProvider, QueryProvider, SchemaProvider
from src.data_access.sql_validator import validate_sql
from src.mlops.dataset import extract_feature_set
from src.mlops.inference import predict_feature_rows
from src.mlops.predictions_store import (
    BACKTEST_TABLE_NAME,
    FORECAST_ACCURACY_VIEW_NAME,
    backtest_table_def,
    ensure_backtest_objects,
    forecast_accuracy_view_def,
    forecast_semantic_tables,
)
from src.mlops.registry import ArtifactRegistry, NoActiveModelError
from src.mlops.split import chronological_split


@runtime_checkable
class BacktestRepository(SchemaProvider, DataProvider, QueryProvider, Protocol):
    """A repository able to host the backtest table/view and read Orders."""


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _replay_test_rows(feature_set: FeatureSet, metadata: ModelRunMetadata) -> list[SalesFeatureRow] | None:
    """The run's exact test split, or None when it cannot be reproduced."""
    fraction = metadata.hyperparameters.get("test_fraction")
    min_rows = metadata.hyperparameters.get("min_test_rows")
    if (feature_set.data_hash != metadata.data_hash
            or not isinstance(fraction, (int, float)) or isinstance(fraction, bool)
            or not isinstance(min_rows, int) or isinstance(min_rows, bool)):
        return None
    try:
        _, test_rows = chronological_split(feature_set.rows, float(fraction), min_rows)
    except ValueError:
        return None
    # Same data + same params is deterministic; still check it matches the run.
    if (len(test_rows) != metadata.test_row_count
            or _utc(test_rows[0].order_date) != _utc(metadata.split_cutoff_date)):
        return None
    return test_rows


def select_holdout(feature_set: FeatureSet, metadata: ModelRunMetadata) -> list[SalesFeatureRow]:
    """Rows the run never trained on, with a known target.

    Exact test split when reproducible; otherwise only rows strictly after the
    cutoff (every training row is on/before it, so those are always unseen).
    """
    rows = _replay_test_rows(feature_set, metadata)
    if rows is None:
        boundary = _utc(metadata.split_cutoff_date)
        rows = [row for row in feature_set.rows if _utc(row.order_date) > boundary]
    return [row for row in rows if row.sales is not None]


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
    force: bool = False,
) -> int:
    """Score the holdout of the run active in `environment` and persist it.

    Returns the number of rows written (`0` when this run was already
    backtested or has no holdout rows). `force=True` deletes this run's rows
    first and scores it again (e.g. after a scoring fix); other runs' history
    is untouched.
    """
    ensure_backtest_objects(repo)
    entry = registry.resolve_active_run(environment)
    if entry is None:
        raise NoActiveModelError(f"No active model promoted in environment {environment!r}.")
    if force:
        repo.delete_rows_where(BACKTEST_TABLE_NAME, "Run ID", entry.run_id)
    elif _has_backtest(repo, entry.run_id):
        return 0

    feature_set = extract_feature_set(repo, orders_table_def)
    holdout = select_holdout(feature_set, registry.read_run_metadata(entry))
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
