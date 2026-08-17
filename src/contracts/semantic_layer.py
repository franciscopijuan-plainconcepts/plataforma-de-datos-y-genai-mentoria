"""Semantic Layer contract models (Pydantic v2).

Defines the typed contracts of the Semantic Layer v2.0:
- `Metric`, `Dimension`, `SemanticRelationship`, `TableSemanticClassification`
  — the declarative artifact content.
- `SemanticLayerDocument` — the top-level artifact produced by the builder.
- `SemanticViewer` — runtime governance context (loaded from `viewers.yaml`).
- `SemanticQueryResolverProtocol` — the typed interface of the pure
  `apply_rls(sql, viewer, table_def) -> str` resolver.

These are the SOLE currency that crosses the Semantic Layer boundary. The
AI Engineering domain (`src/ai_engineering/`) depends on these models and
the `QueryProvider` Protocol — it does NOT import
`src/data_engineering/semantic_layer/` directly (constitution Principle II).
The resolver implementation lives in `src/data_engineering/semantic_layer/`
and is injected at the CLI composition root.

Reference: specs/003-semantic-layer-v1/data-model.md
            specs/003-semantic-layer-v1/contracts/semantic_layer.md
            specs/003-semantic-layer-v1/contracts/integration.md
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable, Union

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.data_access import TableDef


# --- Literal type aliases (constrain legal values) ---

TableName = Literal["Orders", "Returns", "People"]
"""The three tables in the warehouse (from baseline feature 001)."""

TableType = Literal["fact", "dimension", "governance_mapping"]
"""
Classification of each table in the Semantic Layer:
- `fact`               : transactional fact table (Orders, Returns for net sales).
- `dimension`          : dimension table (none in v2.0 — People is governance).
- `governance_mapping` : table used to resolve viewer -> regions (People).
"""

DimensionType = Literal["categorical", "temporal", "geographic"]
"""Type of a Dimension — guides the LLM/consumer on how to group/format it."""

MetricAggregation = Literal[
    "SUM",
    "AVG",
    "COUNT",
    "COUNT_DISTINCT",
    "RATIO",
    "EXPRESSION",
]
"""
Aggregation kind of a Metric:
- `SUM`             : simple SUM(col).
- `AVG`             : AVG(col).
- `COUNT`           : COUNT(col).
- `COUNT_DISTINCT`  : COUNT(DISTINCT col).
- `RATIO`           : numerator / denominator (implies derives_from has 2 entries).
- `EXPRESSION`      : arbitrary SQL expression (used for net_sales, net_profit, ...).
"""

RelationshipCardinality = Literal["1:N", "N:1", "1:1"]
JoinType = Literal["LEFT", "INNER"]


# ---------------------------------------------------------------------------
# Core declarative models
# ---------------------------------------------------------------------------


class Metric(BaseModel):
    """A Semantic Layer metric (a named, computed business quantity).

    Reference: data-model.md § 1.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    business_description: str
    formula_sql: str
    source_table: Literal["Orders"]
    aggregation: MetricAggregation
    derives_from: list[str] = []
    uses_returns: bool = False
    assumption: Union[str, None] = None


class Dimension(BaseModel):
    """A Semantic Layer dimension: a column available for GROUP BY / filtering.

    Reference: data-model.md § 2.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    column: str
    source_table: Literal["Orders"]
    business_description: str
    dimension_type: DimensionType


class SemanticRelationship(BaseModel):
    """A documented join relationship between two warehouse tables.

    Reference: data-model.md § 3.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    from_table: TableName
    from_column: str
    to_table: TableName
    to_column: str
    cardinality: RelationshipCardinality
    join_type: JoinType
    notes: Union[str, None] = None


class TableSemanticClassification(BaseModel):
    """Classification of a warehouse table in the Semantic Layer.

    Reference: data-model.md § 6.
    """

    model_config = ConfigDict(frozen=True)

    name: TableName
    table_type: TableType
    purpose: str


class SemanticViewer(BaseModel):
    """Runtime governance context for a CLI/API caller.

    Reference: data-model.md § 4. Loaded from `viewers.yaml` by the registry.
    """

    model_config = ConfigDict(frozen=True)

    viewer_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    regions: list[str] = []
    allows_full_access: bool = False
    # When `is_local_dev` is False, `allows_full_access` is ignored by the
    # resolver (it always enforces RLS, regardless of the flag).
    is_local_dev: bool = False


class SemanticLayerDocument(BaseModel):
    """The top-level Semantic Layer artifact.

    Reference: data-model.md § 5. Captures tables, metrics, dimensions,
    relationships, provenance hashes, and assumptions. `viewers` are NOT part
    of this document — they are runtime config loaded separately.
    """

    model_config = ConfigDict(frozen=True)

    version: str
    tables: list[TableSemanticClassification]
    metrics: list[Metric]
    dimensions: list[Dimension]
    relationships: list[SemanticRelationship]
    source_sha256: str
    semantic_source_sha256: str
    generated_at: datetime
    assumptions: list[str] = []


# ---------------------------------------------------------------------------
# Resolver Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SemanticQueryResolverProtocol(Protocol):
    """Applies Row-Level Security to a validated SELECT SQL.

    Pure function: no DB calls, no LLM, no state. The caller MUST have
    already validated the SQL via `SqlValidator` before calling this
    method.

    Reference: contracts/semantic_layer.md, contracts/integration.md,
                research.md Part A.
    """

    def apply_rls(
        self,
        sql: str,
        viewer: SemanticViewer,
        table_def: TableDef,
    ) -> str:
        """Return the SQL wrapped with a `Region IN (viewer.regions)` filter.

        - If `viewer.allows_full_access` is True and `viewer.is_local_dev`
          is True, logs a `gov.bypass` event and returns the SQL unchanged.
        - If `viewer.regions` is empty (and not full-access), returns SQL
          producing 0 rows (outer `WHERE FALSE`).
        - Otherwise wraps the input SQL in a subquery and applies an
          outer `WHERE "Region" IN (...)` clause.
        """
        ...


__all__ = [
    "TableName",
    "TableType",
    "DimensionType",
    "MetricAggregation",
    "RelationshipCardinality",
    "JoinType",
    "Metric",
    "Dimension",
    "SemanticRelationship",
    "TableSemanticClassification",
    "SemanticViewer",
    "SemanticLayerDocument",
    "SemanticQueryResolverProtocol",
]
