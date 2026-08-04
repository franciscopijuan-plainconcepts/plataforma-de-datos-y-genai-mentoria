"""Ingestion-pipeline contract models (Pydantic v2).

These models flow through the pipeline:
    pandas.read_excel -> EDA inference -> SchemaInferenceResult
        -> TableDef / ColumnDef (src/contracts/data_access.py)
        -> astype(nullable dtypes) -> TypeAdapter(list[Row]).validate_python(...)
        -> DataProvider.load_rows(table_name, rows) -> LoadResult
        -> LoadArtifactManifest (provenance)

The DataFrame NEVER escapes the ingestion module; only validated Pydantic
models cross boundaries (constitution Principles I, II, III).

Reference: specs/001-data-genai-platform-baseline/contracts/ingestion.md
"""

from __future__ import annotations

from datetime import datetime
from typing import Union

from pydantic import BaseModel, ConfigDict

from src.contracts.data_access import LoadResult, LogicalType


class ColumnProfile(BaseModel):
    """EDA output for a single column."""

    model_config = ConfigDict(frozen=True)

    name: str
    pandas_dtype: str  # observed dtype
    non_null_count: int
    null_count: int
    unique_count: int
    sample_values: list[str]  # first 5 non-null values (for documentation/debug)
    min_value: Union[str, None] = None  # for numeric/date columns
    max_value: Union[str, None] = None  # for numeric/date columns
    is_primary_key_candidate: bool = False
    inferred_logical_type: LogicalType


class TableProfile(BaseModel):
    """EDA output for a single table/sheet."""

    model_config = ConfigDict(frozen=True)

    sheet_name: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile]
    primary_key_candidate: list[str]  # column(s) identified as PK
    duplicate_count_by_pk: dict[str, int] = {}  # e.g., {"Order ID": 63} for Returns


class SharedColumn(BaseModel):
    """A column appearing in more than one sheet (documents relationships)."""

    model_config = ConfigDict(frozen=True)

    name: str
    present_in: list[str]  # sheet names where it appears
    pairwise_overlap: dict[str, int]  # e.g., {"Orders<->Returns": 1970}


class SchemaInferenceResult(BaseModel):
    """Top-level EDA output — the engine-neutral schema description.

    Consumed by the dictionary generator (`DataDictionaryDocument`) and by
    the loader (to derive `TableDef`s and validate row counts).
    """

    model_config = ConfigDict(frozen=True)

    source_file: str
    source_sha256: str  # hash for provenance (manifest)
    tables: list[TableProfile]
    shared_columns: list[SharedColumn]
    inferred_at: datetime


class TableLoadSummary(BaseModel):
    """Per-table load summary, embedded in the load artifact manifest."""

    model_config = ConfigDict(frozen=True)

    table_name: str
    row_count: int  # must match TableProfile.row_count (FR-015 reconciliation)
    column_count: int
    load_result: LoadResult


class LoadArtifactManifest(BaseModel):
    """Provenance manifest emitted by the loader (minimal MLOps footprint).

    Captures per-load provenance so the warehouse is traceable to its source
    and code commit without model-tracking infra (research.md Part B).
    Consumed by the validator (FR-014).
    """

    model_config = ConfigDict(frozen=True)

    source_file: str
    source_sha256: str
    schema_version: str  # EDA-inferred schema version (e.g., "v1")
    loaded_at: datetime
    git_commit: str
    tool_versions: dict[str, str]  # pandas, psycopg, pydantic, etc.
    per_table: list[TableLoadSummary]
