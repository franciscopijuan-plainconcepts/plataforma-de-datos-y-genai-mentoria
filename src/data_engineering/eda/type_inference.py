"""Type-inference helpers for the EDA explorer.

Maps pandas dtypes / column semantics to engine-neutral `LogicalType` values
per research.md Part A & data-model.md. Money columns are mapped to
`DECIMAL(p,s)` (NOT float) — this is a deliberate, documented decision
from research.md Part B.

Reference: specs/001-data-genai-platform-baseline/research.md
            specs/001-data-genai-platform-baseline/contracts/ingestion.md
"""

from __future__ import annotations

import pandas as pd

from src.contracts.data_access import LogicalType


# Money columns are kept as DECIMAL (Decimal in Pydantic), NEVER float.
# Per research.md Part B: "Decimal vs float for money — Decide now or pay
# float-drift debt later."
_MONEY_COLUMNS = frozenset(
    {
        "Sales",
        "Profit",
        "Shipping Cost",
        "Discount",
    }
)


def infer_logical_type(series: pd.Series, column_name: str) -> LogicalType:
    """Infer the engine-neutral `LogicalType` for a pandas Series.

    Rules (grounded in data-model.md):
    - Money columns (Sales/Profit/Shipping Cost/Discount) -> DECIMAL.
    - Other numeric integer-like columns -> INTEGER.
    - datetime columns -> TIMESTAMP.
    - bool columns -> BOOLEAN.
    - Everything else (object/str) -> STRING.
    """
    name = column_name.strip()

    # 1. Money columns — always DECIMAL, even if pandas read them as float64.
    if name in _MONEY_COLUMNS:
        return LogicalType.DECIMAL

    # 2. Datetime columns.
    if pd.api.types.is_datetime64_any_dtype(series):
        return LogicalType.TIMESTAMP

    # 3. Boolean columns (rare in this dataset, but supported).
    if pd.api.types.is_bool_dtype(series):
        return LogicalType.BOOLEAN

    # 4. Integer columns.
    if pd.api.types.is_integer_dtype(series):
        return LogicalType.INTEGER

    # 5. Float columns that are actually integer-like (e.g., Postal Code).
    #    NOTE: Postal Code is handled specially by the schema inferrer
    #    (stored as STRING, not INTEGER), so we don't promote it here.
    if pd.api.types.is_numeric_dtype(series):
        return LogicalType.DECIMAL  # general numeric fallback to DECIMAL

    # 6. Everything else (object / str) -> STRING.
    return LogicalType.STRING


__all__ = ["infer_logical_type"]
