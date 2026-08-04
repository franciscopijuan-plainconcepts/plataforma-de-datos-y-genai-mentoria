"""Data-dictionary contract models (Pydantic v2).

Defines the contracts for generating the comprehensive data dictionary that
integrates Kaggle semantic descriptions (Orders: Transactional Logs;
Returns: Reverse Logistics; People: Sales Governance) with the EDA-discovered
types. Reference: specs/001-data-genai-platform-baseline/contracts/dictionary.md
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict

from src.contracts.data_access import LogicalType


class TableSemantic(BaseModel):
    """Kaggle-level semantic description of a table."""

    model_config = ConfigDict(frozen=True)

    name: str
    kaggle_label: str  # e.g., "Transactional Logs", "Reverse Logistics", "Sales Governance"
    purpose: str  # business description (1-3 sentences)


class ColumnSemantic(BaseModel):
    """Kaggle-level semantic description of a column."""

    model_config = ConfigDict(frozen=True)

    name: str
    business_description: str  # what the column means in business terms
    is_key: bool
    key_kind: Union[Literal["primary", "foreign"], None] = None


class KaggleSemanticSource(BaseModel):
    """Curated table- and column-level semantic descriptions.

    Hardcoded in `src/data_engineering/dictionary/semantic_source.py`.
    """

    model_config = ConfigDict(frozen=True)

    table_semantics: dict[str, TableSemantic]  # keyed by table name


class DictionaryEntry(BaseModel):
    """Per-column dictionary entry — merged EDA + semantic information."""

    model_config = ConfigDict(frozen=True)

    name: str
    business_description: str  # from Kaggle semantic source
    logical_type: LogicalType  # from EDA
    postgres_type: str  # PG adapter mapping (e.g., "NUMERIC(12,4)")
    nullable: bool
    is_key: bool
    key_kind: Union[Literal["primary", "foreign"], None]
    allowed_values: Union[list[str], None] = None
    min_value: Union[str, None] = None
    max_value: Union[str, None] = None
    unique_count: int
    data_quality_notes: list[str] = []  # EDA-derived caveats


class RelationshipEntry(BaseModel):
    """A cross-table relationship documented in the dictionary."""

    model_config = ConfigDict(frozen=True)

    from_column: str
    to_table: str
    to_column: str
    cardinality: Literal["1:N", "N:1", "1:1"]


class TableDictionary(BaseModel):
    """Per-table dictionary section."""

    model_config = ConfigDict(frozen=True)

    name: str
    kaggle_label: str
    purpose: str
    primary_key: list[str]
    relationships: list[RelationshipEntry]
    columns: list[DictionaryEntry]


class DataDictionaryDocument(BaseModel):
    """Top-level data-dictionary document — the generator's output.

    Rendered to a committed `data_dictionary.md` artifact by the CLI
    `generate-dictionary` command. Regeneratable from the loaded schema
    (FR-012). FR-008 requires exactly three tables; FR-009 requires every
    column to have the full entry.
    """

    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    source_file: str
    source_sha256: str  # links to the load manifest
    tables: list[TableDictionary]
