"""`Predictions` table schema + persistence for the predict-sales history.

Every `predict-sales` call persists one row into the `Predictions` SQL
table: the predicted `Sales` value, the date/hour the prediction was made,
which promoted model run served it, and every parameter used as input
(mirrors `PredictionInput`). This is the durable, queryable counterpart to
the `.artifacts/mlops/predict_sales.log` JSONL append-log already written
by `src/mlops/inference.py`.

Engine-neutral: this module builds a `TableDef`/`PredictionRow` and talks
only to the `SchemaProvider`/`DataProvider` Protocols (constitution
Principle III) — no engine-specific (psycopg) imports here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from src.contracts.data_access import ColumnDef, LogicalType, PredictionRow, TableDef
from src.contracts.mlops import PredictionInput, PredictionResult
from src.data_access.interfaces import DataProvider, SchemaProvider


PREDICTIONS_TABLE_NAME = "Predictions"


@runtime_checkable
class PredictionsRepository(SchemaProvider, DataProvider, Protocol):
    """Combined Protocol for a repository that can host `Predictions`.

    `PostgresRepository` already satisfies both `SchemaProvider` and
    `DataProvider`, so it structurally conforms without any changes.
    """


def predictions_table_def() -> TableDef:
    """Engine-neutral `TableDef` for the `Predictions` history table."""
    return TableDef(
        name=PREDICTIONS_TABLE_NAME,
        description=(
            "Historic log of predict-sales inference calls: predicted "
            "Sales, when the prediction was made, which promoted model run "
            "served it, and every input parameter used to predict."
        ),
        columns=[
            ColumnDef(
                name="Prediction ID",
                logical_type=LogicalType.STRING,
                max_length=36,
                nullable=False,
                is_primary_key=True,
            ),
            ColumnDef(name="Predicted At", logical_type=LogicalType.TIMESTAMP, nullable=False),
            ColumnDef(name="Predicted Sales", logical_type=LogicalType.DECIMAL, precision=18, scale=2, nullable=False),
            ColumnDef(name="Run ID", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Model Name", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Environment", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Order Date", logical_type=LogicalType.TIMESTAMP, nullable=False),
            ColumnDef(name="Ship Mode", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Segment", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Region", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Market", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Product ID", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Sub-Category", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Category", logical_type=LogicalType.STRING, nullable=False),
            ColumnDef(name="Quantity", logical_type=LogicalType.INTEGER, nullable=False),
            ColumnDef(name="Discount", logical_type=LogicalType.DECIMAL, precision=10, scale=4, nullable=False),
            ColumnDef(name="Used Fallback Encoding", logical_type=LogicalType.BOOLEAN, nullable=False),
            ColumnDef(name="Latency Ms", logical_type=LogicalType.INTEGER, nullable=False),
        ],
    )


def build_prediction_row(
    prediction_input: PredictionInput, result: PredictionResult
) -> PredictionRow:
    """Build the `PredictionRow` to persist for one predict-sales call."""
    return PredictionRow(
        prediction_id=str(uuid.uuid4()),
        predicted_at=datetime.now(timezone.utc),
        predicted_sales=result.predicted_sales,
        run_id=result.run_id,
        model_name=result.model_name,
        environment=result.environment,
        order_date=prediction_input.order_date,
        ship_mode=prediction_input.ship_mode,
        segment=prediction_input.segment,
        region=prediction_input.region,
        market=prediction_input.market,
        product_id=prediction_input.product_id,
        sub_category=prediction_input.sub_category,
        category=prediction_input.category,
        quantity=prediction_input.quantity,
        discount=prediction_input.discount,
        used_fallback_encoding=result.used_fallback_encoding,
        latency_ms=result.latency_ms,
    )


def ensure_predictions_table(schema_provider: SchemaProvider) -> None:
    """Create the `Predictions` table if it does not already exist (idempotent)."""
    schema_provider.create_table(predictions_table_def())


def persist_prediction(
    provider: PredictionsRepository,
    prediction_input: PredictionInput,
    result: PredictionResult,
) -> None:
    """Insert one historic row for a predict-sales call into `Predictions`.

    Ensures the `Predictions` table exists (idempotent `CREATE TABLE IF NOT
    EXISTS`) before inserting, so this never fails on a fresh database.
    """
    ensure_predictions_table(provider)
    row = build_prediction_row(prediction_input, result)
    provider.load_rows(PREDICTIONS_TABLE_NAME, [row])


__all__ = [
    "PREDICTIONS_TABLE_NAME",
    "PredictionsRepository",
    "build_prediction_row",
    "ensure_predictions_table",
    "persist_prediction",
    "predictions_table_def",
]
