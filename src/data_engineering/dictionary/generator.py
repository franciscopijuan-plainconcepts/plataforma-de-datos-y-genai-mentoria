"""Data dictionary generator — merges EDA-inferred schema with curated Kaggle
semantics into a `DataDictionaryDocument`.

Produces the typed `DataDictionaryDocument` (Pydantic) which the CLI renders
to a committed `data_dictionary.md` artifact. Covers 100% of tables and
columns (FR-008/FR-009), documents cross-table relationships (FR-011), and
attaches EDA-derived data-quality notes (research.md Part A.4).

Reference: specs/001-data-genai-platform-baseline/contracts/dictionary.md
            specs/001-data-genai-platform-baseline/research.md Part A.4
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Mapping, Union

from src.contracts.data_access import ColumnDef, ForeignKeyDef, LogicalType, TableDef
from src.contracts.dictionary import (
    DataDictionaryDocument,
    DictionaryEntry,
    RelationshipEntry,
    TableDictionary,
)
from src.contracts.dictionary import ColumnSemantic
from src.contracts.ingestion import ColumnProfile, SchemaInferenceResult
from src.data_engineering.dictionary.semantic_source import (
    get_column_semantics,
    get_kaggle_semantic_source,
)


# ---------------------------------------------------------------------------
# LogicalType -> human-readable PostgreSQL type string (for the dictionary)
# This mirrors the adapter's `_render_column_ddl` mapping but produces DISPLAY
# strings only — the dictionary is documentation, not DDL execution.
# ---------------------------------------------------------------------------
def _logical_type_to_pg_display(
    col: ColumnProfile, col_def: Union[ColumnDef, None] = None
) -> str:
    """Render the engine-neutral LogicalType + precision/length as a PG display string.

    Prefers the schema-inferrer's `col_def.logical_type` (which carries
    special-case overrides like Postal Code -> STRING) over the raw EDA
    `col_profile.inferred_logical_type` when a `col_def` is provided.
    """
    lt: LogicalType = col_def.logical_type if col_def is not None else col.inferred_logical_type
    if lt is LogicalType.DECIMAL:
        # Prefer explicit precision/scale from the ColumnDef if present.
        if col_def is not None and col_def.precision is not None:
            p = col_def.precision
            s = col_def.scale if col_def.scale is not None else 0
            return f"NUMERIC({p},{s})"
        # Fallbacks by known column name (mirrors data-model.md).
        if col.name == "Sales" or col.name == "Profit":
            return "NUMERIC(12,4)"
        if col.name == "Shipping Cost":
            return "NUMERIC(10,4)"
        if col.name == "Discount":
            return "NUMERIC(5,4)"
        return "NUMERIC"
    if lt is LogicalType.STRING:
        # Prefer the ColumnDef's max_length when present.
        if col_def is not None and col_def.max_length is not None:
            return f"VARCHAR({col_def.max_length})"
        # Fallback by known column name.
        lengths = {
            "Order ID": 50, "Customer ID": 50, "Customer Name": 100,
            "Postal Code": 20, "City": 100, "State": 100, "Country": 100,
            "Region": 50, "Market": 20, "Product ID": 50, "Product Name": 300,
            "Sub-Category": 30, "Category": 30, "Ship Mode": 20, "Segment": 20,
            "Order Priority": 20, "Person": 100, "Returned": 5,
        }
        n = lengths.get(col.name)
        return f"VARCHAR({n})" if n else "VARCHAR"
    if lt is LogicalType.INTEGER:
        # Returns.Return ID is a surrogate SERIAL at load, displayed as SERIAL.
        if col.name == "Return ID":
            return "SERIAL"
        return "INTEGER"
    if lt is LogicalType.TIMESTAMP:
        return "TIMESTAMP"
    if lt is LogicalType.BOOLEAN:
        return "BOOLEAN"
    return str(lt)


# ---------------------------------------------------------------------------
# EDA-derived data-quality notes (research.md Part A.4)
# Keyed by (table, column) -> list of caveat strings.
# ---------------------------------------------------------------------------
_DQ_NOTES: dict[tuple[str, str], list[str]] = {
    ("Orders", "Postal Code"): [
        "80% NULL — only US/Canada rows have postal codes; nullable.",
    ],
    ("Orders", "Discount"): [
        "Fractional amount 0.0–0.85; contains non-round values like 0.402, 0.002 (kept as-is).",
    ],
    ("Orders", "Profit"): [
        "Signed — negative values allowed (losses).",
    ],
    ("Returns", "Returned"): [
        "Degenerate column — always 'Yes'; the row's presence encodes 'returned'.",
    ],
    ("Returns", "Order ID"): [
        "63 duplicate values across 2,033 rows — multi-line returns; Order ID is NOT the PK (surrogate Return ID is).",
    ],
    ("People", "Person"): [
        "Normalized at load: non-breaking spaces (\\xa0) replaced with regular spaces.",
    ],
    ("People", "Region"): [
        "Region taxonomy mismatch — People splits Canada into Eastern/Western Canada (24 regions) vs Orders' single Canada (23 regions); 22 of 24 overlap. Kept as VARCHAR, not enum — resolution is v2.0 Semantic-Layer scope.",
    ],
    ("Orders", "Region"): [
        "Region taxonomy mismatch with People (see People.Region note); 22 of 24 People regions overlap with Orders. Kept as VARCHAR pending v2.0 Semantic-Layer consolidation.",
    ],
}


def _cardinality_for(from_table: str, from_col: str, to_table: str) -> Literal["1:N", "N:1", "1:1"]:
    """Infer cardinality label for a documented relationship."""
    # Returns.Order ID -> Orders.Order ID : many returns can share an order's id (N:1)
    if from_table == "Returns" and from_col == "Order ID" and to_table == "Orders":
        return "N:1"
    # People.Region -> Orders.Region : one person's region covers many orders (1:N)
    if from_table == "People" and to_table in ("Orders", "Returns"):
        return "1:N"
    # Returns.Region -> People.Region : many returns refer to one person's region (N:1)
    if from_table == "Returns" and from_col == "Region" and to_table == "People":
        return "N:1"
    return "N:1"


def _build_relationships(table_name: str, foreign_keys: list[ForeignKeyDef]) -> list[RelationshipEntry]:
    """Convert a TableDef's foreign_keys into documented RelationshipEntry items."""
    rels: list[RelationshipEntry] = []
    for fk in foreign_keys:
        rels.append(
            RelationshipEntry(
                from_column=fk.column,
                to_table=fk.references_table,
                to_column=fk.references_column,
                cardinality=_cardinality_for(table_name, fk.column, fk.references_table),
            )
        )
    return rels


def _build_entry(
    table_name: str,
    col_profile: ColumnProfile,
    col_def: Union[ColumnDef, None],
    col_semantics_by_name: Mapping[str, ColumnSemantic],
) -> DictionaryEntry:
    """Build a single DictionaryEntry by merging EDA + semantic + DQ notes."""
    semantics_obj = col_semantics_by_name.get(col_profile.name)
    business_description = "—"
    is_key = False
    # key_kind is a Literal constrained to "primary" / "foreign" / None.
    key_kind: Union[Literal["primary", "foreign"], None] = None
    if semantics_obj is not None:
        business_description = semantics_obj.business_description
        is_key = semantics_obj.is_key
        # The semantic source already constrains key_kind to the same Literal;
        # assign directly (no cast needed now that the type matches).
        key_kind = semantics_obj.key_kind

    # If the schema inferrer marked a column as PK, honor that as the key flag too.
    if col_def is not None and col_def.is_primary_key:
        is_key = True
        if key_kind is None:
            key_kind = "primary"

    # EDA-derived allowed values for low-cardinality enum-like columns.
    allowed_values: Union[list[str], None] = None
    if (
        col_profile.inferred_logical_type is LogicalType.STRING
        and 0 < col_profile.unique_count <= 30
        and col_profile.sample_values
    ):
        # Document the sample as a seed of the enumeration (full enumeration
        # is captured by the EDA stats; the dictionary records the seed).
        allowed_values = list(col_profile.sample_values)

    dq_notes = list(_DQ_NOTES.get((table_name, col_profile.name), []))

    # Prefer the schema-inferrer's LogicalType (carries Postal Code -> STRING
    # override) over the raw EDA type when a ColumnDef is available.
    resolved_logical_type: LogicalType = (
        col_def.logical_type if col_def is not None else col_profile.inferred_logical_type
    )

    return DictionaryEntry(
        name=col_profile.name,
        business_description=business_description,
        logical_type=resolved_logical_type,
        postgres_type=_logical_type_to_pg_display(col_profile, col_def),
        nullable=col_profile.null_count > 0,
        is_key=is_key,
        key_kind=key_kind,
        allowed_values=allowed_values,
        min_value=col_profile.min_value,
        max_value=col_profile.max_value,
        unique_count=col_profile.unique_count,
        data_quality_notes=dq_notes,
    )


def generate_dictionary(
    inference: SchemaInferenceResult,
    table_defs: list[TableDef],
) -> DataDictionaryDocument:
    """Generate a `DataDictionaryDocument` from the EDA + inferred schema.

    Merges:
    - EDA `ColumnProfile`s (types, stats, nullability),
    - inferred `TableDef`s (PK + FK relationships), and
    - curated Kaggle semantics (table labels, column descriptions, key flags),
    and attaches EDA-derived data-quality notes (research.md Part A.4).
    """
    semantic = get_kaggle_semantic_source()
    # Index table defs by name for FK/PK lookup.
    defs_by_name: dict[str, TableDef] = {td.name: td for td in table_defs}
    # Index column defs by (table, col) for PK flag lookup.
    col_defs_by_key: dict[tuple[str, str], ColumnDef] = {}
    for td in table_defs:
        for c in td.columns:
            col_defs_by_key[(td.name, c.name)] = c

    tables: list[TableDictionary] = []
    for table_profile in inference.tables:
        name = table_profile.sheet_name
        t_semantic = semantic.table_semantics.get(name)
        kaggle_label = t_semantic.kaggle_label if t_semantic else name
        purpose = t_semantic.purpose if t_semantic else f"Table {name}."

        # PK columns from the inferred TableDef.
        td_obj = defs_by_name.get(name)
        pk_columns: list[str] = []
        fks: list[ForeignKeyDef] = []
        if td_obj is not None:
            pk_columns = [c.name for c in td_obj.columns if c.is_primary_key]
            fks = list(td_obj.foreign_keys)

        # Per-column semantics, indexed by name.
        col_semantics = {c.name: c for c in get_column_semantics(name)}

        # Build one DictionaryEntry per EDA profiled column.
        # NOTE for Returns: the surrogate Return ID is NOT in the EDA profile
        # (it's added by the schema inferrer). We add it explicitly so the
        # dictionary documents 100% of the loaded columns.
        entries: list[DictionaryEntry] = []
        seen_names: set[str] = set()
        for cp in table_profile.columns:
            cd: Union[ColumnDef, None] = col_defs_by_key.get((name, cp.name))
            entries.append(_build_entry(name, cp, cd, col_semantics))
            seen_names.add(cp.name)

        # Append any inferred columns not in the EDA profile (e.g. Return ID).
        if td_obj is not None:
            for c in td_obj.columns:
                if c.name in seen_names:
                    continue
                # Build a synthetic profile for the inferred-only column.
                synth_profile = ColumnProfile(
                    name=c.name,
                    pandas_dtype="(surrogate)",
                    non_null_count=table_profile.row_count,
                    null_count=0,
                    unique_count=table_profile.row_count,
                    sample_values=[],
                    min_value=None,
                    max_value=None,
                    is_primary_key_candidate=c.is_primary_key,
                    inferred_logical_type=c.logical_type,
                )
                cd2: Union[ColumnDef, None] = col_defs_by_key.get((name, c.name))
                entries.append(_build_entry(name, synth_profile, cd2, col_semantics))

        rels = _build_relationships(name, fks)

        tables.append(
            TableDictionary(
                name=name,
                kaggle_label=kaggle_label,
                purpose=purpose,
                primary_key=pk_columns,
                relationships=rels,
                columns=entries,
            )
        )

    return DataDictionaryDocument(
        generated_at=datetime.now(timezone.utc),
        source_file=inference.source_file,
        source_sha256=inference.source_sha256,
        tables=tables,
    )


__all__ = ["generate_dictionary"]
