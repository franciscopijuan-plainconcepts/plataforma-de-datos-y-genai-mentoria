"""Contract test for dictionary generation (constitution-mandated).

Verifies the `DataDictionaryDocument` produced by `generate_dictionary`
satisfies the contract requirements (FR-008 through FR-012):
- exactly three tables (Orders, Returns, People),
- 100% of columns covered with the required fields (name, business_description,
  type, nullable, is_key, key_kind),
- the three cross-table relationships documented.

This test uses the REAL source workbook `Global Superstore Data.xlsx` so the
generated dictionary is validated against the actual schema (no mocks).

Reference: specs/001-data-genai-platform-baseline/contracts/dictionary.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.contracts.data_access import LogicalType
from src.contracts.dictionary import (
    DataDictionaryDocument,
    DictionaryEntry,
    RelationshipEntry,
    TableDictionary,
)
from src.data_engineering.dictionary.generator import generate_dictionary
from src.data_engineering.eda.explorer import explore_workbook
from src.data_engineering.eda.schema_inferrer import infer_table_defs
from src.data_engineering.ingestion.manifest import sha256_of_file


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_FILE = _REPO_ROOT / "Global Superstore Data.xlsx"

# Expected EDA-derived column counts (incl. the surrogate Return ID for Returns)
# per data-model.md.
_EXPECTED_TABLES = {"Orders": 24, "Returns": 4, "People": 2}


@pytest.fixture(scope="module")
def dictionary_document() -> DataDictionaryDocument:
    """Generate the dictionary once for the whole module (uses the real .xlsx)."""
    if not _SOURCE_FILE.exists():
        pytest.skip(f"Source workbook not found: {_SOURCE_FILE}")
    tables, shared = explore_workbook(_SOURCE_FILE)
    from datetime import datetime, timezone

    from src.contracts.ingestion import SchemaInferenceResult

    inference = SchemaInferenceResult(
        source_file=str(_SOURCE_FILE),
        source_sha256=sha256_of_file(_SOURCE_FILE),
        tables=tables,
        shared_columns=shared,
        inferred_at=datetime.now(timezone.utc),
    )
    table_defs = infer_table_defs(tables)
    return generate_dictionary(inference, table_defs)


# ---------------------------------------------------------------------------
# FR-008: exactly three tables, all columns covered
# ---------------------------------------------------------------------------

def test_dictionary_covers_exactly_three_tables(
    dictionary_document: DataDictionaryDocument,
) -> None:
    """FR-008: the dictionary documents exactly Orders/Returns/People."""
    table_names = {t.name for t in dictionary_document.tables}
    assert table_names == {"Orders", "Returns", "People"}, (
        f"Expected {{Orders, Returns, People}}, got {table_names}"
    )


def test_each_table_has_expected_column_count(
    dictionary_document: DataDictionaryDocument,
) -> None:
    """FR-008: every table documents 100% of its columns (incl. surrogate Return ID)."""
    for table in dictionary_document.tables:
        expected = _EXPECTED_TABLES[table.name]
        actual = len(table.columns)
        assert actual == expected, (
            f"{table.name}: expected {expected} columns in the dictionary, got {actual}"
        )


# ---------------------------------------------------------------------------
# FR-009: every DictionaryEntry has the required fields populated
# ---------------------------------------------------------------------------

def test_every_entry_has_required_fields(
    dictionary_document: DataDictionaryDocument,
) -> None:
    """FR-009: name, business_description, type, nullable, is_key, key_kind on every entry."""
    for table in dictionary_document.tables:
        for entry in table.columns:
            assert entry.name, f"Entry in {table.name} missing name"
            assert entry.business_description, (
                f"{table.name}.{entry.name} missing business_description"
            )
            assert entry.logical_type in LogicalType, (
                f"{table.name}.{entry.name} has invalid logical_type {entry.logical_type!r}"
            )
            assert entry.postgres_type, (
                f"{table.name}.{entry.name} missing postgres_type"
            )
            assert isinstance(entry.nullable, bool), (
                f"{table.name}.{entry.name} nullable not a bool"
            )
            assert isinstance(entry.is_key, bool), (
                f"{table.name}.{entry.name} is_key not a bool"
            )
            # key_kind is either None, 'primary', or 'foreign'.
            assert entry.key_kind in (None, "primary", "foreign"), (
                f"{table.name}.{entry.name} has invalid key_kind {entry.key_kind!r}"
            )


# ---------------------------------------------------------------------------
# FR-010: every TableDictionary has name, purpose (label), primary_key, relationships
# ---------------------------------------------------------------------------

def test_every_table_has_metadata(
    dictionary_document: DataDictionaryDocument,
) -> None:
    """FR-010: each table has a Kaggle label, purpose, primary_key, relationships."""
    for table in dictionary_document.tables:
        assert table.kaggle_label, f"{table.name} missing kaggle_label"
        assert table.purpose, f"{table.name} missing purpose"
        assert table.primary_key, f"{table.name} missing primary_key"
        # relationships is a list (may be empty for People).
        assert isinstance(table.relationships, list)


def test_primary_keys_are_correct(
    dictionary_document: DataDictionaryDocument,
) -> None:
    """data-model.md PKs: Orders=Row ID, Returns=Return ID (surrogate), People=Person."""
    pk_by_table = {t.name: set(t.primary_key) for t in dictionary_document.tables}
    assert pk_by_table["Orders"] == {"Row ID"}
    assert pk_by_table["Returns"] == {"Return ID"}
    assert pk_by_table["People"] == {"Person"}


# ---------------------------------------------------------------------------
# FR-011: the three cross-table relationships are documented
# ---------------------------------------------------------------------------

def test_cross_table_relationships_documented(
    dictionary_document: DataDictionaryDocument,
) -> None:
    """FR-011: Returns.Order ID -> Orders.Order ID; People.Region -> Orders/Returns.Region."""
    # Flatten all RelationshipEntries across all tables.
    all_rels: list[RelationshipEntry] = []
    for table in dictionary_document.tables:
        all_rels.extend(table.relationships)

    # Build a set of (from_table, from_column, to_table, to_column) tuples.
    # The relationships are documented on the CHILD side (FKs pointing to the
    # referenced table): Orders.Region -> People.Region, Returns.Region ->
    # People.Region, Returns.Order ID -> Orders.Order ID. People is a root
    # (it has no outbound FKs).
    rel_keys = {
        (table.name, r.from_column, r.to_table, r.to_column)
        for table in dictionary_document.tables
        for r in table.relationships
    }

    expected_rels = {
        # Returns -> Orders (a return refers to an order).
        ("Returns", "Order ID", "Orders", "Order ID"),
        # Orders -> People (an order's region is governed by People).
        ("Orders", "Region", "People", "Region"),
        # Returns -> People (a return's region is governed by People).
        ("Returns", "Region", "People", "Region"),
    }
    missing = expected_rels - rel_keys
    assert not missing, f"Missing documented relationships: {missing}"


# ---------------------------------------------------------------------------
# FR-012: regeneratable (deterministic from the schema)
# ---------------------------------------------------------------------------

def test_dictionary_is_regeneratable(
    dictionary_document: DataDictionaryDocument,
) -> None:
    """FR-012: generating twice from the same schema yields the same structure."""
    tables, shared = explore_workbook(_SOURCE_FILE)
    from datetime import datetime, timezone

    from src.contracts.ingestion import SchemaInferenceResult

    inference2 = SchemaInferenceResult(
        source_file=str(_SOURCE_FILE),
        source_sha256=sha256_of_file(_SOURCE_FILE),
        tables=tables,
        shared_columns=shared,
        inferred_at=datetime.now(timezone.utc),
    )
    table_defs2 = infer_table_defs(tables)
    doc2 = generate_dictionary(inference2, table_defs2)

    # Structure must match: same tables, same column counts, same PKs, same rels.
    assert len(doc2.tables) == len(dictionary_document.tables)
    for t1, t2 in zip(dictionary_document.tables, doc2.tables):
        assert t1.name == t2.name
        assert len(t1.columns) == len(t2.columns)
        assert set(t1.primary_key) == set(t2.primary_key)
        assert set((r.from_column, r.to_table) for r in t1.relationships) == set(
            (r.from_column, r.to_table) for r in t2.relationships
        )


# ---------------------------------------------------------------------------
# Data-quality notes (research.md Part A.4) attached
# ---------------------------------------------------------------------------

def test_dq_notes_attached(dictionary_document: DataDictionaryDocument) -> None:
    """DQ notes from research.md A.4 are attached to the relevant columns."""
    notes_by_key: dict[tuple[str, str], list[str]] = {
        (t.name, c.name): c.data_quality_notes
        for t in dictionary_document.tables
        for c in t.columns
    }
    # Postal Code must mention 80% NULL.
    assert any("80% NULL" in n for n in notes_by_key.get(("Orders", "Postal Code"), [])), (
        "Orders.Postal Code DQ note must mention 80% NULL"
    )
    # Profit must mention signed/negative.
    assert any("egative" in n or "igned" in n for n in notes_by_key.get(("Orders", "Profit"), [])), (
        "Orders.Profit DQ note must mention signed/negative values"
    )
    # Returns.Order ID must mention the 63 duplicates.
    assert any("63 duplicate" in n for n in notes_by_key.get(("Returns", "Order ID"), [])), (
        "Returns.Order ID DQ note must mention the 63 duplicates"
    )
    # People.Person must mention normalization.
    assert any("ormaliz" in n for n in notes_by_key.get(("People", "Person"), [])), (
        "People.Person DQ note must mention normalization"
    )
