"""Data-access contract models (Pydantic v2).

These models are the sole currency that crosses the data-access boundary.
Raw dict / DBAPI rows MUST NOT cross this boundary (constitution Principle I).

All types are engine-neutral. Engine-specific rendering (PostgreSQL DDL,
BigQuery schema API) happens only inside adapter implementations
(`src/data_access/adapters/<engine>/`) per Principle III.

Reference: specs/001-data-genai-platform-baseline/contracts/data_access.md
            specs/001-data-genai-platform-baseline/data-model.md
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Union

from pydantic import BaseModel, ConfigDict, Field


# A Row is any of the typed per-table row models. This union is the input
# type for `DataProvider.load_rows` so a single semantic bulk-load method
# can accept any table's rows without leaking engine-specific concerns.
Row = Union["OrderRow", "ReturnRow", "PersonRow", "PredictionRow"]


class LogicalType(str, Enum):
    """Engine-neutral logical type carried on every `ColumnDef`.

    Each adapter maps a `LogicalType` to its engine-specific DDL type:
    - PostgreSQL: INTEGER, NUMERIC(p,s), VARCHAR(n)/TEXT, TIMESTAMP, BOOLEAN
    - BigQuery (future): INT64, NUMERIC, STRING, TIMESTAMP, BOOL
    """

    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"  # carries precision + scale
    STRING = "STRING"  # carries max_length
    TIMESTAMP = "TIMESTAMP"
    BOOLEAN = "BOOLEAN"


class ForeignKeyDef(BaseModel):
    """A foreign-key-like relationship from a local column to a target table/column."""

    model_config = ConfigDict(frozen=True)

    column: str
    references_table: str
    references_column: str


class ColumnDef(BaseModel):
    """Engine-neutral column definition (output of schema inference)."""

    model_config = ConfigDict(frozen=True)

    name: str
    logical_type: LogicalType
    precision: Union[int, None] = None  # for DECIMAL
    scale: Union[int, None] = None  # for DECIMAL
    max_length: Union[int, None] = None  # for STRING -> PG VARCHAR(n)
    nullable: bool
    is_primary_key: bool = False
    allowed_values: Union[list[str], None] = None  # for enum-like STRING columns


class TableDef(BaseModel):
    """Engine-neutral table definition (consumed by `SchemaProvider.create_table`)."""

    model_config = ConfigDict(frozen=True)

    name: str
    columns: list[ColumnDef]
    description: str  # business description (from Kaggle semantics)
    foreign_keys: list[ForeignKeyDef] = []


class LoadResult(BaseModel):
    """Outcome of a bulk-load operation from `DataProvider.load_rows`.

    Per FR-015, the first invalid row raises a ValidationError before this
    result is produced — `rows_rejected` should normally be 0; non-zero
    indicates adapter-side rejection (e.g., constraint violation), reported
    in `errors`.
    """

    model_config = ConfigDict(frozen=True)

    table_name: str
    rows_loaded: int
    rows_rejected: int = 0
    errors: list[str] = []


# --- Row models (one per table) ---
# Typed Pydantic v2 models carrying a single loaded row. Money fields are
# `Decimal` (NOT float) per research.md Part B. See data-model.md for the
# per-column rationale and EDA evidence.


class OrderRow(BaseModel):
    """One row of the `Orders` table (Transactional Logs).

    Reference: data-model.md § 1. `Row ID` is the PK (EDA-confirmed unique).
    `postal_code` is `str | None` because EDA found 80% NULL (only US/Canada
    rows). Money fields (Sales/Profit/Shipping Cost/Discount) are `Decimal`.
    """

    # `populate_by_name=True` lets callers construct via the snake_case field
    # name OR the Excel source column alias (title-case with spaces).
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    row_id: int = Field(validation_alias="Row ID")
    order_id: str = Field(validation_alias="Order ID")
    order_date: datetime = Field(validation_alias="Order Date")
    ship_date: datetime = Field(validation_alias="Ship Date")
    ship_mode: str = Field(validation_alias="Ship Mode")
    customer_id: str = Field(validation_alias="Customer ID")
    customer_name: str = Field(validation_alias="Customer Name")
    segment: str = Field(validation_alias="Segment")
    postal_code: Union[str, None] = Field(default=None, validation_alias="Postal Code")  # 80% NULL in EDA
    city: str = Field(validation_alias="City")
    state: str = Field(validation_alias="State")
    country: str = Field(validation_alias="Country")
    region: str = Field(validation_alias="Region")  # FK-like -> People.Region (future v2.0 RLS anchor)
    market: str = Field(validation_alias="Market")
    product_id: str = Field(validation_alias="Product ID")
    product_name: str = Field(validation_alias="Product Name")
    sub_category: str = Field(validation_alias="Sub-Category")
    category: str = Field(validation_alias="Category")
    sales: Decimal = Field(validation_alias="Sales")  # money — Decimal, NOT float
    quantity: int = Field(validation_alias="Quantity")
    discount: Decimal = Field(validation_alias="Discount")  # money/fraction — Decimal, NOT float
    profit: Decimal = Field(validation_alias="Profit")  # money, signed — Decimal, NOT float
    shipping_cost: Decimal = Field(validation_alias="Shipping Cost")  # money — Decimal, NOT float
    order_priority: str = Field(validation_alias="Order Priority")


class ReturnRow(BaseModel):
    """One row of the `Returns` table (Reverse Logistics).

    Reference: data-model.md § 2. `Order ID` is NOT unique (63 duplicates in
    EDA) so a surrogate `return_id` is assigned at load. `returned` is kept
    as `str` (not Literal["Yes"]) to detect source-data drift.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    # `return_id` is a surrogate assigned by the loader (not a source Excel
    # column). It uses `validation_alias="Return ID"` so the loader-injected
    # key and the DB column name ("Return ID", from the schema inferrer) align.
    return_id: int = Field(validation_alias="Return ID")
    returned: str = Field(validation_alias="Returned")  # EDA: always "Yes"; kept as str to detect drift
    order_id: str = Field(validation_alias="Order ID")  # FK-like -> Orders.Order ID (non-unique; multi-line returns)
    region: str = Field(validation_alias="Region")  # FK-like -> People.Region


class PersonRow(BaseModel):
    """One row of the `People` table (Sales Governance).

    Reference: data-model.md § 3. `person` is the PK. Validated rows MUST
    have non-breaking spaces normalized at load (see loader).
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    person: str = Field(validation_alias="Person")  # PK; normalized (non-breaking spaces stripped) at load
    region: str = Field(validation_alias="Region")  # future v2.0 RLS anchor


class PredictionRow(BaseModel):
    """One row of the `Predictions` table (historic log of predict-sales calls).

    Every call to `predict-sales` inserts one row: the predicted `Sales`
    value, the date/hour the prediction was made (`predicted_at`), which
    promoted model run served it, and every parameter that was used as
    input to the model (mirrors `PredictionInput` in `src/contracts/mlops.py`).
    `prediction_id` is a surrogate UUID4 assigned at insert time (no natural
    key exists for a prediction event).
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    prediction_id: str = Field(validation_alias="Prediction ID")
    predicted_at: datetime = Field(validation_alias="Predicted At")  # date + hour the prediction was made
    predicted_sales: Decimal = Field(validation_alias="Predicted Sales")
    run_id: str = Field(validation_alias="Run ID")
    model_name: str = Field(validation_alias="Model Name")
    environment: str = Field(validation_alias="Environment")
    # --- input parameters used to produce the prediction ---
    order_date: datetime = Field(validation_alias="Order Date")
    ship_mode: str = Field(validation_alias="Ship Mode")
    segment: str = Field(validation_alias="Segment")
    region: str = Field(validation_alias="Region")
    market: str = Field(validation_alias="Market")
    product_id: str = Field(validation_alias="Product ID")
    sub_category: str = Field(validation_alias="Sub-Category")
    category: str = Field(validation_alias="Category")
    quantity: int = Field(validation_alias="Quantity")
    discount: Decimal = Field(validation_alias="Discount")
    used_fallback_encoding: bool = Field(validation_alias="Used Fallback Encoding")
    latency_ms: int = Field(validation_alias="Latency Ms")
