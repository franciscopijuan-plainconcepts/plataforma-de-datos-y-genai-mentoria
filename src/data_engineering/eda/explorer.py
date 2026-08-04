"""Excel EDA explorer — inspects `Global Superstore Data.xlsx` and produces
per-sheet profiles (TableProfile) for schema inference.

This is one of the ONLY modules (alongside `data_engineering.ingestion`)
allowed to import `pandas` / `openpyxl` (see contracts/ingestion.md
boundary rules and tests/contract/test_boundaries.py). The DataFrame
NEVER escapes this module — only validated Pydantic `TableProfile` models
cross the boundary.

Reference: specs/001-data-genai-platform-baseline/research.md Part A
            specs/001-data-genai-platform-baseline/contracts/ingestion.md
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd

from src.contracts.ingestion import ColumnProfile, SharedColumn, TableProfile
from src.data_engineering.eda.type_inference import infer_logical_type


def _safe_str(value: object) -> str:
    """Best-effort stringify for sample values (robust to NaN / mixed types)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _profile_column(series: pd.Series, table_name: str) -> ColumnProfile:
    """Build a `ColumnProfile` for a single pandas Series (column)."""
    name = str(series.name)
    pandas_dtype = str(series.dtype)
    non_null = int(series.notna().sum())
    null = int(series.isna().sum())
    unique = int(series.nunique(dropna=True))

    # First 5 non-null sample values (for documentation/debug).
    samples = [
        _safe_str(v)
        for v in series.dropna().head(5).tolist()
    ]

    # Min / max: for numeric or datetime columns only, as strings.
    min_value: str | None = None
    max_value: str | None = None
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        dropped = series.dropna()
        if len(dropped) > 0:
            min_value = _safe_str(dropped.min())
            max_value = _safe_str(dropped.max())

    # PK candidate: unique + non-null + integer-like + fully covers the table.
    is_pk_candidate = bool(
        unique == non_null
        and null == 0
        and unique > 0
        and (
            pd.api.types.is_integer_dtype(series)
            or (
                pd.api.types.is_numeric_dtype(series)
                and (series.dropna() % 1 == 0).all()
            )
        )
    )

    # Special-case the surrogate-PK need for Returns.Order ID (duplicates).
    if table_name == "Returns" and name == "Order ID":
        is_pk_candidate = False

    return ColumnProfile(
        name=name,
        pandas_dtype=pandas_dtype,
        non_null_count=non_null,
        null_count=null,
        unique_count=unique,
        sample_values=samples,
        min_value=min_value,
        max_value=max_value,
        is_primary_key_candidate=is_pk_candidate,
        inferred_logical_type=infer_logical_type(series, name),
    )


def _profile_table(sheet_name: str, df: pd.DataFrame) -> TableProfile:
    """Build a `TableProfile` for one sheet."""
    n_rows, n_cols = df.shape
    columns = [_profile_column(df[c], sheet_name) for c in df.columns]

    # PK candidate columns.
    pk_candidates = [c.name for c in columns if c.is_primary_key_candidate]

    # Duplicate counts for any non-unique column that "looks like" an ID
    # (so the schema inferrer knows to use a surrogate PK for Returns).
    duplicate_counts: dict[str, int] = {}
    for col in columns:
        # Heuristic: columns named like an ID that aren't unique.
        name_lower = col.name.lower()
        if "id" in name_lower or name_lower == "order id":
            if col.unique_count < col.non_null_count:
                duplicate_counts[col.name] = int(
                    df[col.name].duplicated().sum()
                )

    return TableProfile(
        sheet_name=sheet_name,
        row_count=int(n_rows),
        column_count=int(n_cols),
        columns=columns,
        primary_key_candidate=pk_candidates,
        duplicate_count_by_pk=duplicate_counts,
    )


def _detect_shared_columns(tables: list[TableProfile]) -> list[SharedColumn]:
    """Find columns appearing in more than one sheet and report pairwise overlap."""
    # Map column name -> list of sheet names that contain it.
    col_to_sheets: dict[str, list[str]] = {}
    for t in tables:
        for c in t.columns:
            col_to_sheets.setdefault(c.name, []).append(t.sheet_name)

    shared: list[SharedColumn] = []
    for col_name, sheets in col_to_sheets.items():
        if len(sheets) <= 1:
            continue
        # Pairwise overlap of the actual string-coerced values.
        # (We re-read just the needed columns lazily; the explorer is the
        #  only pandas-touching module so this is acceptable.)
        # Build a memo of value-sets per (sheet, col) lazily — but we don't
        # have the DataFrame here. For the contract we report presence and
        # leave pairwise_overlap populated from the caller's knowledge.
        # The loader/dictionary will reconcile actual value overlap; here we
        # document WHICH sheets share the column.
        shared.append(
            SharedColumn(
                name=col_name,
                present_in=sheets,
                pairwise_overlap={},  # populated by explore_workbook (has DataFrames)
            )
        )
    return shared


def explore_workbook(source_file: str | Path) -> tuple[list[TableProfile], list[SharedColumn]]:
    """Read an .xlsx workbook and return (per-sheet profiles, shared columns).

    Returns `(tables, shared_columns)`. Raises `FileNotFoundError` (FR-013
    fail-fast) if the file does not exist, and re-raises pandas/openpyxl
    parse errors so the bootstrap can surface them clearly.
    """
    path = Path(source_file)
    if not path.exists():
        raise FileNotFoundError(f"Source workbook not found: {path}")

    # Read all sheets at once so we can compute shared-column overlap.
    sheets: dict[str, pd.DataFrame] = pd.read_excel(path, sheet_name=None, engine="openpyxl")

    tables = [_profile_table(name, df) for name, df in sheets.items()]

    # Now compute shared-column pairwise overlap using the actual data.
    shared = _detect_shared_columns_with_data(tables, sheets)
    return tables, shared


def _detect_shared_columns_with_data(
    tables: list[TableProfile], sheets: dict[str, pd.DataFrame]
) -> list[SharedColumn]:
    """Compute shared columns with actual pairwise value overlap (str-coered)."""
    col_to_sheets: dict[str, list[str]] = {}
    for t in tables:
        for c in t.columns:
            col_to_sheets.setdefault(c.name, []).append(t.sheet_name)

    shared: list[SharedColumn] = []
    for col_name, sheet_list in col_to_sheets.items():
        if len(sheet_list) <= 1:
            continue
        # Value sets per sheet.
        val_sets: dict[str, set[str]] = {}
        for s in sheet_list:
            df = sheets[s]
            if col_name in df.columns:
                vals = df[col_name].dropna().astype(str).unique().tolist()
                val_sets[s] = set(vals)
            else:  # pragma: no cover - defensive
                val_sets[s] = set()

        pairwise: dict[str, int] = {}
        for i in range(len(sheet_list)):
            for j in range(i + 1, len(sheet_list)):
                a, b = sheet_list[i], sheet_list[j]
                inter = val_sets[a] & val_sets[b]
                pairwise[f"{a}<->{b}"] = len(inter)

        shared.append(
            SharedColumn(
                name=col_name,
                present_in=sheet_list,
                pairwise_overlap=pairwise,
            )
        )
    return shared


__all__ = ["explore_workbook"]
