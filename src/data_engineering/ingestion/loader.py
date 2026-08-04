"""Ingestion loader — Excel → typed Pydantic models → data-access layer.

This is the ONLY module (alongside `data_engineering.eda`) that may import
`pandas` / `openpyxl` (see contracts/ingestion.md boundary rules). The
DataFrame NEVER escapes this module — only validated Pydantic row models
cross the boundary (constitution Principle I).

Pipeline (per research.md Part B):
    pandas.read_excel -> astype(nullable dtypes)
        -> TypeAdapter(list[Row]).validate_python(...)
        -> DataProvider.load_rows(table_name, rows)

Failure handling (FR-013 / FR-015): the first invalid row raises a
ValidationError with the offending row path — no silent partial load.

Reference: specs/001-data-genai-platform-baseline/contracts/ingestion.md
            specs/001-data-genai-platform-baseline/research.md Part B
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Union

import pandas as pd
from pydantic import TypeAdapter

from src.contracts.data_access import OrderRow, PersonRow, ReturnRow, Row
from src.contracts.data_access import LoadResult  # noqa: F401  (re-exported for callers)
from src.data_access.interfaces import DataProvider, SchemaProvider
from src.data_engineering.eda.explorer import explore_workbook
from src.data_engineering.eda.schema_inferrer import infer_table_defs


# Mapping sheet name -> row model.
_ROW_MODEL: dict[str, type[Row]] = {
    "Orders": OrderRow,
    "Returns": ReturnRow,
    "People": PersonRow,
}

# pandas nullable-dtype mapping for clean decimal/int handling.
# (research.md Part B: use nullable dtypes so NaN -> None on to_dict.)
_DTYPE_COERCE: dict[str, str] = {
    "Sales": "object",        # Decimal via converter
    "Profit": "object",       # Decimal via converter
    "Shipping Cost": "object",  # Decimal via converter
    "Discount": "object",     # Decimal via converter
    "Postal Code": "string",  # nullable string
}


def _coerce_money(series: pd.Series) -> pd.Series:
    """Convert a pandas Series to Python `Decimal` per-element (so Pydantic
    `Decimal` fields validate cleanly)."""
    def to_decimal(v: object) -> Union[Decimal, None]:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return Decimal(str(v))
    return series.map(to_decimal)


def _normalize_person_name(name: object) -> str:
    """Normalize a People.Person value: non-breaking spaces -> regular space,
    strip leading/trailing whitespace (per data-model.md § 3, research.md A.4)."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    return str(name).replace("\xa0", " ").strip()


def _prepare_orders_df(df: pd.DataFrame) -> list[dict[str, object]]:
    """Coerce an Orders DataFrame into rows of validated-ready dicts."""
    df = df.copy()
    # Money columns -> Decimal via object dtype.
    for col in ("Sales", "Profit", "Shipping Cost", "Discount"):
        if col in df.columns:
            df[col] = _coerce_money(df[col])
    # Postal Code as nullable string.
    if "Postal Code" in df.columns:
        df["Postal Code"] = df["Postal Code"].astype("string")
    return df.to_dict(orient="records")  # type: ignore[no-any-return]


def _prepare_returns_df(df: pd.DataFrame) -> list[dict[str, object]]:
    """Coerce a Returns DataFrame and assign the surrogate `Return ID`.

    Per data-model.md § 2: `Order ID` has 63 duplicates, so a surrogate
    `Return ID` (1..N row-ordinal) is assigned at load. The column is
    inserted under the title-case source-style name to match the DB column
    (created by the schema inferrer) and the model's `validation_alias`.
    """
    df = df.copy()
    df = df.reset_index(drop=True)
    df.insert(0, "Return ID", range(1, len(df) + 1))
    return df.to_dict(orient="records")  # type: ignore[no-any-return]


def _prepare_people_df(df: pd.DataFrame) -> list[dict[str, object]]:
    """Coerce a People DataFrame and normalize Person names."""
    df = df.copy()
    if "Person" in df.columns:
        df["Person"] = df["Person"].map(_normalize_person_name)
    return df.to_dict(orient="records")  # type: ignore[no-any-return]


_PREPARERS = {
    "Orders": _prepare_orders_df,
    "Returns": _prepare_returns_df,
    "People": _prepare_people_df,
}


def load_workbook(
    source_file: Union[str, Path],
    schema_provider: SchemaProvider,
    data_provider: DataProvider,
) -> dict[str, LoadResult]:
    """Load an .xlsx workbook into the warehouse via the data-access layer.

    Steps:
    1. EDA: explore_workbook(source_file) -> TableProfiles.
    2. Schema: infer_table_defs -> create each TableDef.
    3. Load: read each sheet, coerce, validate via TypeAdapter, load_rows.

    Returns a `{table_name: LoadResult}` dict. Per FR-015, the first invalid
    row raises ValidationError with the offending path — no partial load.
    """
    # 1 + 2. EDA + schema inference + materialization.
    tables, _shared = explore_workbook(source_file)
    table_defs = infer_table_defs(tables)
    for td in table_defs:
        # Idempotent reload (FR-005): drop any existing table first so the
        # load is deterministic regardless of prior state. Safe because we
        # are reloading the full dataset from the Excel source every time.
        schema_provider.drop_table(td.name)
        schema_provider.create_table(td)

    # Read all sheets once for loading.
    sheets: dict[str, pd.DataFrame] = pd.read_excel(
        Path(source_file), sheet_name=None, engine="openpyxl"
    )

    results: dict[str, LoadResult] = {}
    for table_def in table_defs:
        name = table_def.name
        df = sheets.get(name)
        if df is None:
            raise KeyError(f"Sheet {name!r} not found in {source_file}")
        preparer = _PREPARERS.get(name)
        if preparer is None:
            raise ValueError(f"No row-model/preparer for table {name!r}")
        row_model = _ROW_MODEL[name]
        records = preparer(df)

        # Validate the whole batch at the boundary (TypeAdapter raises on the
        # first invalid row with the offending path — FR-015 fail-fast).
        # Re-raise the original ValidationError so the offending-row path is
        # preserved verbatim (do not re-wrap and lose the location info).
        adapter: TypeAdapter[list[Row]] = TypeAdapter(list[row_model])  # type: ignore[valid-type]
        validated_rows: list[Row] = adapter.validate_python(records)

        # Bulk-load validated rows. The DataFrame never crosses this point.
        result = data_provider.load_rows(name, validated_rows)
        results[name] = result

    return results


__all__ = ["load_workbook", "LoadResult"]
