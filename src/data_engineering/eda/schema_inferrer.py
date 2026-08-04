"""Schema inferrer — converts EDA `TableProfile`s into engine-neutral
`TableDef` / `ColumnDef`s ready for materialization by the data-access layer.

Grounded in data-model.md: the surrogate `Return ID` is introduced for
Returns (because `Order ID` has 63 duplicates), money columns map to
`DECIMAL(p,s)`, Postal Code is forced to STRING (preserving leading zeros),
and FK-like relationships are attached (Returns.Order ID -> Orders.Order ID;
People.Region -> Orders/Returns.Region).

Reference: specs/001-data-genai-platform-baseline/data-model.md
            specs/001-data-genai-platform-baseline/contracts/data_access.md
"""

from __future__ import annotations

from src.contracts.data_access import (
    ColumnDef,
    ForeignKeyDef,
    LogicalType,
    TableDef,
)
from src.contracts.ingestion import ColumnProfile, TableProfile


# --- Decimal precision/scale per column (from data-model.md) ---
# Money columns use explicit (precision, scale); Discount uses (5,4).
_DECIMAL_PRECISION: dict[str, tuple[int, int]] = {
    "Sales": (12, 4),
    "Profit": (12, 4),
    "Shipping Cost": (10, 4),
    "Discount": (5, 4),
}

# --- VARCHAR max lengths per free-text column (from data-model.md) ---
_STRING_LENGTHS: dict[str, int] = {
    "Order ID": 50,
    "Customer ID": 50,
    "Customer Name": 100,
    "Postal Code": 20,
    "City": 100,
    "State": 100,
    "Country": 100,
    "Region": 50,
    "Market": 20,
    "Product ID": 50,
    "Product Name": 300,
    "Sub-Category": 30,
    "Category": 30,
    "Ship Mode": 20,
    "Segment": 20,
    "Order Priority": 20,
    "Person": 100,
    "Returned": 5,
}

# Columns that are legitimately low-cardinality enums (documented allowed
# values) per data-model.md. We only attach allowed_values when the EDA
# confirms <= 30 uniques.
_ENUMLIKE_COLUMNS = frozenset(
    {
        "Ship Mode",
        "Segment",
        "Market",
        "Category",
        "Sub-Category",
        "Order Priority",
    }
)

# Threshold for treating a STRING column as enum-like (<= N uniques).
_ENUM_CARDINALITY_THRESHOLD = 30


def _column_def_from_profile(col_profile: ColumnProfile, table_name: str) -> ColumnDef:
    """Build a `ColumnDef` from a `ColumnProfile`, applying data-model rules."""
    name = col_profile.name
    logical_type = col_profile.inferred_logical_type

    # Special case: Postal Code is STRONGLY typed as STRING even though
    # pandas read it as float64 (it has leading zeros / postal format).
    if name == "Postal Code":
        logical_type = LogicalType.STRING

    # Determine nullable. data-model.md: only Postal Code is nullable
    # (80% NULL in EDA); every other column is NOT NULL.
    nullable = name == "Postal Code" or col_profile.null_count > 0

    # Decimal precision/scale for money columns.
    precision: int | None = None
    scale: int | None = None
    if logical_type is LogicalType.DECIMAL and name in _DECIMAL_PRECISION:
        precision, scale = _DECIMAL_PRECISION[name]

    # VARCHAR max_length for STRING columns.
    max_length: int | None = None
    if logical_type is LogicalType.STRING and name in _STRING_LENGTHS:
        max_length = _STRING_LENGTHS[name]

    # Enum-like allowed_values for low-cardinality documented enums.
    allowed_values: list[str] | None = None
    if (
        logical_type is LogicalType.STRING
        and name in _ENUMLIKE_COLUMNS
        and col_profile.unique_count <= _ENUM_CARDINALITY_THRESHOLD
        and col_profile.unique_count > 0
    ):
        # Use the EDA sample values (first 5) as documentation seeds — the
        # real allowed-value enumeration happens via the dictionary generator.
        # We don't store all values here to keep the TableDef compact; the
        # dictionary document captures the full enumeration from EDA stats.
        allowed_values = None  # documented in the dictionary, not enforced in DDL

    # PK flag — determined per-table in the inferrer (see infer_table_defs).
    is_pk = False

    return ColumnDef(
        name=name,
        logical_type=logical_type,
        precision=precision,
        scale=scale,
        max_length=max_length,
        nullable=nullable,
        is_primary_key=is_pk,
        allowed_values=allowed_values,
    )


def _orders_table_def(profile: TableProfile) -> TableDef:
    """Build the `Orders` table: PK = Row ID."""
    columns = [
        _column_def_from_profile(c, "Orders") for c in profile.columns
    ]
    # Set Row ID as PK.
    for col in columns:
        if col.name == "Row ID":
            col_init = col.model_copy(update={"is_primary_key": True})
            columns[columns.index(col)] = col_init
            break

    return TableDef(
        name="Orders",
        columns=columns,
        description="Transactional Logs — one row per order line.",
        foreign_keys=[
            ForeignKeyDef(
                column="Region",
                references_table="People",
                references_column="Region",
            ),
        ],
    )


def _returns_table_def(profile: TableProfile) -> TableDef:
    """Build the `Returns` table: surrogate `Return ID` PK (not Order ID)."""
    # Prepend the surrogate Return ID column (assigned at load by the loader).
    return_id_col = ColumnDef(
        name="Return ID",
        logical_type=LogicalType.INTEGER,
        nullable=False,
        is_primary_key=True,
    )
    columns: list[ColumnDef] = [return_id_col]
    for c in profile.columns:
        if c.name == "Returned":
            columns.append(
                _column_def_from_profile(c, "Returns")
            )
        else:
            columns.append(_column_def_from_profile(c, "Returns"))

    return TableDef(
        name="Returns",
        columns=columns,
        description="Reverse Logistics — records which orders were returned.",
        foreign_keys=[
            ForeignKeyDef(
                column="Order ID",
                references_table="Orders",
                references_column="Order ID",
            ),
            ForeignKeyDef(
                column="Region",
                references_table="People",
                references_column="Region",
            ),
        ],
    )


def _people_table_def(profile: TableProfile) -> TableDef:
    """Build the `People` table: PK = Person."""
    columns = [
        _column_def_from_profile(c, "People") for c in profile.columns
    ]
    for col in columns:
        if col.name == "Person":
            col_init = col.model_copy(update={"is_primary_key": True})
            columns[columns.index(col)] = col_init
            break

    return TableDef(
        name="People",
        columns=columns,
        description="Sales Governance — regional sales-person mapping.",
        foreign_keys=[],
    )


_TABLE_BUILDERS = {
    "Orders": _orders_table_def,
    "Returns": _returns_table_def,
    "People": _people_table_def,
}


def infer_table_defs(profiles: list[TableProfile]) -> list[TableDef]:
    """Convert EDA `TableProfile`s into engine-neutral `TableDef`s.

    Returns one `TableDef` per table, with the PK/FK rules from
    data-model.md applied (Row ID for Orders, surrogate Return ID for
    Returns, Person for People; FK-like relationships attached).
    """
    table_defs: list[TableDef] = []
    for profile in profiles:
        builder = _TABLE_BUILDERS.get(profile.sheet_name)
        if builder is None:
            # Unknown sheet — fall back to a generic table with the first
            # PK candidate as PK. Defensive; not expected for this dataset.
            columns = [
                _column_def_from_profile(c, profile.sheet_name)
                for c in profile.columns
            ]
            pk_candidates = profile.primary_key_candidate
            if pk_candidates:
                pk_name = pk_candidates[0]
                for i, col in enumerate(columns):
                    if col.name == pk_name:
                        columns[i] = col.model_copy(update={"is_primary_key": True})
                        break
            table_defs.append(
                TableDef(
                    name=profile.sheet_name,
                    columns=columns,
                    description=f"Table {profile.sheet_name}.",
                    foreign_keys=[],
                )
            )
        else:
            table_defs.append(builder(profile))
    return table_defs


__all__ = ["infer_table_defs"]
