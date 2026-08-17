"""Unit tests for the Semantic Layer builder (v2.0).

Validates:
- The canonical 8 metrics + 11 dimensions + 2 relationships build a valid
  `SemanticLayerDocument` from a real `DataDictionaryDocument`.
- Builds are reproducible (same inputs => same JSON via the renderer).
- FR-006 fail-fast: a metric referencing an unknown column raises ValueError.

Reference: specs/003-semantic-layer-v1/tasks.md T013
            specs/003-semantic-layer-v1/data-model.md § Validation Rules
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.contracts.data_access import (
    ColumnDef,
    ForeignKeyDef,
    LogicalType,
    TableDef,
)
from src.contracts.dictionary import (
    DataDictionaryDocument,
    DictionaryEntry,
    RelationshipEntry,
    TableDictionary,
)
from src.data_engineering.semantic_layer.builder import SemanticLayerBuilder
from src.data_engineering.semantic_layer.render import render_json


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEMANTIC_SOURCE_PATH = _REPO_ROOT / "src" / "data_engineering" / "dictionary" / "semantic_source.py"


def _build_test_table_defs() -> list[TableDef]:
    """Build minimal in-memory `TableDef`s with the columns the metrics/dimensions need."""
    orders_columns = [
        ColumnDef(name="Row ID", logical_type=LogicalType.INTEGER, nullable=False, is_primary_key=True),
        ColumnDef(name="Order ID", logical_type=LogicalType.STRING, max_length=50, nullable=False),
        ColumnDef(name="Order Date", logical_type=LogicalType.TIMESTAMP, nullable=False),
        ColumnDef(name="Region", logical_type=LogicalType.STRING, max_length=50, nullable=False),
        ColumnDef(name="Country", logical_type=LogicalType.STRING, max_length=100, nullable=False),
        ColumnDef(name="Market", logical_type=LogicalType.STRING, max_length=20, nullable=False),
        ColumnDef(name="Segment", logical_type=LogicalType.STRING, max_length=20, nullable=False),
        ColumnDef(name="Category", logical_type=LogicalType.STRING, max_length=30, nullable=False),
        ColumnDef(name="Sub-Category", logical_type=LogicalType.STRING, max_length=30, nullable=False),
        ColumnDef(name="Ship Mode", logical_type=LogicalType.STRING, max_length=20, nullable=False),
        ColumnDef(name="Order Priority", logical_type=LogicalType.STRING, max_length=20, nullable=False),
        ColumnDef(name="Customer Name", logical_type=LogicalType.STRING, max_length=100, nullable=False),
        ColumnDef(name="Product Name", logical_type=LogicalType.STRING, max_length=300, nullable=False),
        ColumnDef(name="Sales", logical_type=LogicalType.DECIMAL, precision=12, scale=4, nullable=False),
        ColumnDef(name="Profit", logical_type=LogicalType.DECIMAL, precision=12, scale=4, nullable=False),
        ColumnDef(name="Discount", logical_type=LogicalType.DECIMAL, precision=5, scale=4, nullable=False),
        ColumnDef(name="Quantity", logical_type=LogicalType.INTEGER, nullable=False),
    ]
    returns_columns = [
        ColumnDef(name="Return ID", logical_type=LogicalType.INTEGER, nullable=False, is_primary_key=True),
        ColumnDef(name="Returned", logical_type=LogicalType.STRING, max_length=5, nullable=False),
        ColumnDef(name="Order ID", logical_type=LogicalType.STRING, max_length=50, nullable=False,
                   foreign_keys=[]),
        ColumnDef(name="Region", logical_type=LogicalType.STRING, max_length=50, nullable=False),
    ]
    people_columns = [
        ColumnDef(name="Person", logical_type=LogicalType.STRING, max_length=100, nullable=False,
                   is_primary_key=True),
        ColumnDef(name="Region", logical_type=LogicalType.STRING, max_length=50, nullable=False),
    ]
    return [
        TableDef(name="Orders", columns=orders_columns, description="Orders table.",
                  foreign_keys=[
                      ForeignKeyDef(column="Region", references_table="People", references_column="Region")
                  ]),
        TableDef(name="Returns", columns=returns_columns, description="Returns table.",
                  foreign_keys=[
                      ForeignKeyDef(column="Order ID", references_table="Orders", references_column="Order ID"),
                      ForeignKeyDef(column="Region", references_table="People", references_column="Region"),
                  ]),
        TableDef(name="People", columns=people_columns, description="People table."),
    ]


def _build_test_dictionary(table_defs: list[TableDef]) -> DataDictionaryDocument:
    """Build an in-memory `DataDictionaryDocument` matching `table_defs` (no EDA needed)."""
    tables: list[TableDictionary] = []
    for td in table_defs:
        columns: list[DictionaryEntry] = []
        for c in td.columns:
            columns.append(DictionaryEntry(
                name=c.name,
                business_description=f"Column {c.name}",
                logical_type=c.logical_type,
                postgres_type=c.logical_type.value,
                nullable=c.nullable,
                is_key=c.is_primary_key,
                key_kind="primary" if c.is_primary_key else None,
                allowed_values=None,
                min_value=None,
                max_value=None,
                unique_count=0,
                data_quality_notes=[],
            ))
        rels: list[RelationshipEntry] = []
        for fk in td.foreign_keys:
            rels.append(RelationshipEntry(
                from_column=fk.column,
                to_table=fk.references_table,
                to_column=fk.references_column,
                cardinality="N:1",
            ))
        tables.append(TableDictionary(
            name=td.name,
            kaggle_label="label",
            purpose=f"Purpose of {td.name}",
            primary_key=[],
            relationships=rels,
            columns=columns,
        ))
    return DataDictionaryDocument(
        generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        source_file="test",
        source_sha256="test-source-sha",
        tables=tables,
    )


@pytest.fixture()
def built_document() -> object:
    """Build a canonical SemanticLayerDocument from the test fixture dictionary."""
    table_defs = _build_test_table_defs()
    dictionary = _build_test_dictionary(table_defs)
    sem_sha = "test-semantic-source-sha"
    src_sha = "test-source-sha"
    builder = SemanticLayerBuilder()
    return builder.build(
        dictionary=dictionary,
        semantic_source_sha256=sem_sha,
        source_sha256=src_sha,
    )


def test_builder_produces_canonical_metrics(built_document: object) -> None:
    """FR-003: the builder produces exactly the 8 canonical metrics."""
    metric_names = {m.name for m in built_document.metrics}  # type: ignore[attr-defined]
    expected = {
        "gross_sales", "returned_amount", "net_sales", "return_rate",
        "total_profit", "net_profit", "avg_order_value", "order_count",
    }
    assert metric_names == expected


def test_builder_produces_canonical_dimensions(built_document: object) -> None:
    """FR-004: at least 11 dimensions (region, country, segment, ...)."""
    assert len(built_document.dimensions) >= 11  # type: ignore[attr-defined]
    dim_names = {d.name for d in built_document.dimensions}  # type: ignore[attr-defined]
    assert "region" in dim_names
    assert "order_date" in dim_names
    assert "customer" in dim_names


def test_builder_produces_two_relationships(built_document: object) -> None:
    """FR-005: both Orders-Returns (by Order ID) and Orders-People (by Region)."""
    names = {r.name for r in built_document.relationships}  # type: ignore[attr-defined]
    assert names == {"orders_to_returns", "orders_to_people_by_region"}
    # Validate that Returns->Orders by Order ID is recorded.
    ret_rel = next(r for r in built_document.relationships  # type: ignore[attr-defined]
                    if r.name == "orders_to_returns")
    assert ret_rel.from_table == "Returns"
    assert ret_rel.from_column == "Order ID"
    assert ret_rel.to_table == "Orders"
    assert ret_rel.to_column == "Order ID"


def test_builder_fails_fast_on_unknown_formula_column() -> None:
    """FR-006: a metric whose formula_sql references an unknown column raises."""
    from src.contracts.semantic_layer import Metric

    # Hand-build a dictionary missing the "Profit" column, but keep Sales so that
    # other formulas stay valid; we want a single failure here.
    table_defs = _build_test_table_defs()
    # Remove the Profit column from the Orders table.
    orders_def = next(t for t in table_defs if t.name == "Orders")
    orders_def_no_profit = TableDef(
        name=orders_def.name,
        columns=[c for c in orders_def.columns if c.name != "Profit"],
        description=orders_def.description,
        foreign_keys=orders_def.foreign_keys,
    )
    table_defs = [t if t.name != "Orders" else orders_def_no_profit for t in table_defs]
    dictionary = _build_test_dictionary(table_defs)
    builder = SemanticLayerBuilder()
    with pytest.raises(ValueError, match="total_profit"):
        builder.build(
            dictionary=dictionary,
            semantic_source_sha256="x",
            source_sha256="y",
        )


def test_builds_against_real_dictionary_from_disk() -> None:
    """End-to-end sanity: build against the real `semantic_source.py` + a fixture dictionary.

    Confirms the builder is wired correctly without requiring Docker EDA.
    """
    table_defs = _build_test_table_defs()
    dictionary = _build_test_dictionary(table_defs)
    sem_sha = sha256_of_file_str(_SEMANTIC_SOURCE_PATH)
    src_sha = "real-source-sha"
    builder = SemanticLayerBuilder()
    doc = builder.build(
        dictionary=dictionary,
        semantic_source_sha256=sem_sha,
        source_sha256=src_sha,
    )
    assert doc.semantic_source_sha256 == sem_sha
    assert len(doc.metrics) == 8


def test_builder_json_is_deterministic(built_document: object) -> None:
    """FR-007: building twice from the same inputs produces byte-identical JSON.

    SC-005: the JSON excludes `generated_at` so the timestamp doesn't break
    determinism across runs on the same inputs.
    """
    table_defs = _build_test_table_defs()
    dictionary = _build_test_dictionary(table_defs)
    sem_sha = "test-semantic-source-sha"
    src_sha = "test-source-sha"
    builder = SemanticLayerBuilder()
    doc1 = builder.build(
        dictionary=dictionary, semantic_source_sha256=sem_sha, source_sha256=src_sha
    )
    doc2 = builder.build(
        dictionary=dictionary, semantic_source_sha256=sem_sha, source_sha256=src_sha
    )
    assert render_json(doc1) == render_json(doc2)


def test_json_excludes_generated_at(built_document: object) -> None:
    """FR-007 / SC-005: the canonical JSON must NOT contain `generated_at`."""
    json_str = render_json(built_document)  # type: ignore[arg-type]
    assert "generated_at" not in json_str, (
        "Canonical JSON must exclude generated_at for determinism (FR-007)."
    )


def test_json_contains_provenance_hash(built_document: object) -> None:
    """The JSON includes the two provenance hashes (source + semantic_source)."""
    json_str = render_json(built_document)  # type: ignore[arg-type]
    assert "test-source-sha" in json_str
    assert "test-semantic-source-sha" in json_str


# --- Helpers ---


def sha256_of_file_str(path: Path) -> str:
    """Hash a file's bytes to a hex sha256 string (utility for test seeds)."""
    from src.data_engineering.ingestion.manifest import sha256_of_file as _real

    return _real(path)
