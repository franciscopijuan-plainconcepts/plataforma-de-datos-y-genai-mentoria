"""Semantic Layer builder (v2.0).

Constructs the `SemanticLayerDocument` from:
- `metrics.py` (canonical 8 metrics)
- the existing `DataDictionaryDocument` (source of column truth)
- the existing `semantic_source.py` (table purposes)

Build-time validation (fail-fast, FR-006):
- Every identifier quoted with double-quotes in any `formula_sql` MUST exist
  as a column in the relevant table of the `DataDictionaryDocument`.
- Every `SemanticRelationship.from_column` / `.to_column` MUST exist in the
  corresponding table of the dictionary.
- Every `Metric.derives_from[*]` MUST exist in the metric list being built.
- No duplicate metric/dimension/relationship names.

The builder is pure: no DB, no LLM, no file I/O. It accepts a
`DataDictionaryDocument` + provenance hashes and returns a
`SemanticLayerDocument`.

Reference: specs/003-semantic-layer-v1/contracts/semantic_layer.md
            specs/003-semantic-layer-v1/data-model.md § Validation Rules
            specs/003-semantic-layer-v1/tasks.md T009, T013
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Mapping

from src.contracts.data_access import ForeignKeyDef
from src.contracts.dictionary import DataDictionaryDocument, TableDictionary
from src.contracts.semantic_layer import (
    Dimension,
    Metric,
    SemanticLayerDocument,
    SemanticRelationship,
    TableSemanticClassification,
)
from src.data_engineering.dictionary.semantic_source import (
    get_kaggle_semantic_source,
)
from src.data_engineering.semantic_layer.metrics import get_metrics


# ---------------------------------------------------------------------------
# Canonical dimensions (FR-004).
#
# Hard-coded mappings of dimension_name -> (column, dimension_type, business_description).
# The `column` references must exist in the Orders table of the dictionary.
# The builder validates this at build-time and fails fast if missing.
# ---------------------------------------------------------------------------
# (name, column, dimension_type, business_description)
_CANONICAL_DIMENSIONS: list[tuple[str, str, str, str]] = [
    (
        "region",
        "Region",
        "geographic",
        "Geographic region of the order. Shared with Returns and People — the basis for RLS.",
    ),
    (
        "country",
        "Country",
        "geographic",
        "Country of the delivery address.",
    ),
    (
        "market",
        "Market",
        "geographic",
        "Top-level market the order belongs to (Asia Pacific, Europe, Africa, LATAM, USCA).",
    ),
    (
        "segment",
        "Segment",
        "categorical",
        "Customer segment (consumer, corporate, home office).",
    ),
    (
        "category",
        "Category",
        "categorical",
        "Top-level product category (Furniture, Office Supplies, Technology).",
    ),
    (
        "sub_category",
        "Sub-Category",
        "categorical",
        "Product sub-category (e.g., Binders, Chairs, Phones).",
    ),
    (
        "ship_mode",
        "Ship Mode",
        "categorical",
        "Shipping class selected for the order.",
    ),
    (
        "order_priority",
        "Order Priority",
        "categorical",
        "Priority level of the order (Low, Medium, High, Critical).",
    ),
    (
        "order_date",
        "Order Date",
        "temporal",
        "Date the order was placed.",
    ),
    (
        "customer",
        "Customer Name",
        "categorical",
        "Name of the customer who placed the order.",
    ),
    (
        "product",
        "Product Name",
        "categorical",
        "Human-readable product name.",
    ),
]


# ---------------------------------------------------------------------------
# Canonical cross-table relationships (FR-005).
#
# Hard-coded from `data_dictionary.md` Cross-Table Relationships section.
# ---------------------------------------------------------------------------
# (name, from_table, from_column, to_table, to_column, cardinality, join_type, notes)
_CANONICAL_RELATIONSHIPS: list[
    tuple[str, str, str, str, str, str, str, str | None]
] = [
    (
        "orders_to_returns",
        "Returns",
        "Order ID",
        "Orders",
        "Order ID",
        "N:1",
        "LEFT",
        'Returns."Order ID" has 63 duplicate values across 2,033 rows (multi-line '
        "returns) — use EXISTS instead of direct JOIN to avoid inflating Orders rows.",
    ),
    (
        "orders_to_people_by_region",
        "People",
        "Region",
        "Orders",
        "Region",
        "1:N",
        "INNER",
        "Taxonomy mismatch: People splits Canada into Eastern/Western Canada vs "
        "Orders' single Canada (23 unique). Resolution is v3.0+ scope; the resolver "
        "is conservative — a viewer scoped to 'Eastern Canada' sees 0 Orders rows.",
    ),
]


# ---------------------------------------------------------------------------
# Helpers for validation
# ---------------------------------------------------------------------------

_QUOTED_IDENTIFIER_RE = re.compile(r'"([^"]+)"')


def _columns_for_table(
    dictionary: DataDictionaryDocument, table_name: str
) -> set[str]:
    """Return the set of all column names (preserving case) for `table_name`.

    Returns an empty set if the table is not in the dictionary.
    """
    for td in dictionary.tables:
        if td.name.lower() == table_name.lower():
            return {entry.name for entry in td.columns}
    return set()


def _table_purpose(from_semantic_source: Mapping[str, str], table_name: str) -> str:
    """Pull a table's purpose line from the semantic source (Kaggle semantics)."""
    return from_semantic_source.get(table_name, "")


def _validate_formula_columns(
    formula_sql: str, allowed_columns: set[str], metric_name: str
) -> None:
    """FR-006: every quoted identifier in `formula_sql` must be a known column.

    Raises `ValueError` if any quoted identifier is not in `allowed_columns`.
    Bare table names (e.g., `Returns` written without quotes in the EXISTS
    subquery) and SQL keywords are not checked here — they are validated
    separately by the SqlValidator at LLM-SQL-execution time.
    """
    referenced = set(_QUOTED_IDENTIFIER_RE.findall(formula_sql))
    unknown = referenced - allowed_columns
    if unknown:
        raise ValueError(
            f"Metric {metric_name!r} references unknown column(s): "
            f"{sorted(unknown)}. Allowed (Orders + Returns + People columns): "
            f"{sorted(allowed_columns)}. The builder refuses to produce a "
            "SemanticLayerDocument that references non-existent columns (FR-006)."
        )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


class SemanticLayerBuilder:
    """Builds a `SemanticLayerDocument` from the existing data sources.

    Build-time validation is fail-fast. The builder never produces a document
    that references unknown columns or violates the metric closure.
    """

    def build(
        self,
        dictionary: DataDictionaryDocument,
        semantic_source_sha256: str,
        source_sha256: str,
        document_version: str = "1.0.0",
    ) -> SemanticLayerDocument:
        """Construct and validate the Semantic Layer document.

        Args:
            dictionary: the existing `DataDictionaryDocument` from feature 001
                (the source of truth for columns/types/relationships).
            semantic_source_sha256: sha256 of the `semantic_source.py` source
                — preserves code provenance for the artifact.
            source_sha256: sha256 of the warehouse load manifest source file
                — preserved warehouse state provenance.
            document_version: semver of the artifact content; bump when
                `metrics.py` or `_CANONICAL_DIMENSIONS` changes.

        Returns:
            A fully validated `SemanticLayerDocument`.

        Raises:
            ValueError: if a metric formula, dimension column, or relationship
                column references a column not present in `dictionary`.
        """
        # 1. Collect the columns available across all 3 tables for formula lookup.
        orders_columns = _columns_for_table(dictionary, "Orders")
        returns_columns = _columns_for_table(dictionary, "Returns")
        people_columns = _columns_for_table(dictionary, "People")
        all_columns = orders_columns | returns_columns | people_columns
        if not orders_columns:
            raise ValueError(
                "DataDictionaryDocument does not contain an Orders table. "
                "Cannot build the Semantic Layer."
            )

        # 2. Build metric models with column-existence validation.
        metrics = get_metrics()
        for m in metrics:
            _validate_formula_columns(m.formula_sql, all_columns, m.name)

        # 3. Validate metric closure: every derives_from[*] must be a known metric.
        #    RATIO aggregation must reference exactly two derives_from entries.
        known_metric_names = {m.name for m in metrics}
        for m in metrics:
            for parent in m.derives_from:
                if parent not in known_metric_names:
                    raise ValueError(
                        f"Metric {m.name!r} derives_from unknown metric "
                        f"{parent!r}. Known metrics: {sorted(known_metric_names)}."
                    )
            if m.aggregation == "RATIO" and len(m.derives_from) != 2:
                raise ValueError(
                    f"Metric {m.name!r} uses aggregation=RATIO but has "
                    f"{len(m.derives_from)} derives_from entries (expected 2: "
                    "numerator + denominator)."
                )

        # 4. Build dimensions, validating column existence against Orders.
        dimensions: list[Dimension] = []
        seen_dim_names: set[str] = set()
        for name, column, dim_type, desc in _CANONICAL_DIMENSIONS:
            if name in seen_dim_names:
                raise ValueError(f"Duplicate dimension name: {name!r}")
            if column not in orders_columns:
                raise ValueError(
                    f"Dimension {name!r} references unknown Orders column "
                    f"{column!r}. Known Orders columns: {sorted(orders_columns)}."
                )
            dimensions.append(
                Dimension(
                    name=name,
                    column=column,
                    source_table="Orders",
                    business_description=desc,
                    dimension_type=dim_type,
                )
            )
            seen_dim_names.add(name)

        # 5. Build relationships, validating column existence against dictionary.
        relationships: list[SemanticRelationship] = []
        seen_rel_names: set[str] = set()
        for (
            r_name,
            from_table,
            from_column,
            to_table,
            to_column,
            cardinality,
            join_type,
            notes,
        ) in _CANONICAL_RELATIONSHIPS:
            if r_name in seen_rel_names:
                raise ValueError(f"Duplicate relationship name: {r_name!r}")
            from_cols = _columns_for_table(dictionary, from_table)
            to_cols = _columns_for_table(dictionary, to_table)
            if from_column not in from_cols:
                raise ValueError(
                    f"Relationship {r_name!r} from_column {from_column!r} not "
                    f"found in {from_table} columns: {sorted(from_cols)}."
                )
            if to_column not in to_cols:
                raise ValueError(
                    f"Relationship {r_name!r} to_column {to_column!r} not "
                    f"found in {to_table} columns: {sorted(to_cols)}."
                )
            relationships.append(
                SemanticRelationship(
                    name=r_name,
                    from_table=from_table,  # type: ignore[arg-type]
                    from_column=from_column,
                    to_table=to_table,  # type: ignore[arg-type]
                    to_column=to_column,
                    cardinality=cardinality,  # type: ignore[arg-type]
                    join_type=join_type,  # type: ignore[arg-type]
                    notes=notes,
                )
            )
            seen_rel_names.add(r_name)

        # 6. Build table classifications (purposes sourced from semantic_source).
        semantic_source = get_kaggle_semantic_source()
        purposes: dict[str, str] = {
            name: ts.purpose
            for name, ts in semantic_source.table_semantics.items()
        }
        tables: list[TableSemanticClassification] = []
        for tname, table_type, _purpose in [
            ("Orders", "fact", "Transactional fact table."),
            ("Returns", "fact", "Reverse logistics — returned order lines."),
            ("People", "governance_mapping", "Region-to-person mapping (RLS anchor)."),
        ]:
            purpose = _table_purpose(purposes, tname)
            if not purpose:
                # Fall back to the dictionary's purpose if semantic_source missing.
                td: TableDictionary | None = None
                for d in dictionary.tables:
                    if d.name.lower() == tname.lower():
                        td = d
                        break
                purpose = td.purpose if td is not None else _purpose
            tables.append(
                TableSemanticClassification(
                    name=tname,  # type: ignore[arg-type]
                    table_type=table_type,  # type: ignore[arg-type]
                    purpose=purpose,
                )
            )

        # 7. Document-level assumptions (for human-readable artifact).
        assumptions: list[str] = []
        for m in metrics:
            if m.assumption is not None:
                assumptions.append(f"{m.name}: {m.assumption}")

        return SemanticLayerDocument(
            version=document_version,
            tables=tables,
            metrics=metrics,
            dimensions=dimensions,
            relationships=relationships,
            source_sha256=source_sha256,
            semantic_source_sha256=semantic_source_sha256,
            generated_at=datetime.now(timezone.utc),
            assumptions=assumptions,
        )


__all__ = ["SemanticLayerBuilder"]
