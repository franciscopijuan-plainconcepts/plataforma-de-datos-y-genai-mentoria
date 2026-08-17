"""Contract tests for Semantic Layer models (constitution-mandated gate).

Asserts:
- All models in `src/contracts/semantic_layer.py` are Pydantic v2 with explicit types.
- `SemanticLayerDocument` constructed from the canonical metrics + dimensions
  + relationships satisfies build-time validation expectations.
- `SemanticViewer` honors `allows_full_access` + `is_local_dev` semantics.
- `SemanticQueryResolverProtocol` is a runtime_checkable Protocol.

Reference: specs/003-semantic-layer-v1/contracts/semantic_layer.md
            specs/003-semantic-layer-v1/tasks.md T007, T014, T019
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from src.contracts.semantic_layer import (
    Dimension,
    Metric,
    SemanticLayerDocument,
    SemanticQueryResolverProtocol,
    SemanticRelationship,
    SemanticViewer,
    TableSemanticClassification,
)
from src.data_engineering.semantic_layer.metrics import get_metrics


# --- All models are Pydantic v2 ---

@pytest.mark.parametrize(
    "model_cls",
    [
        Metric,
        Dimension,
        SemanticRelationship,
        TableSemanticClassification,
        SemanticViewer,
        SemanticLayerDocument,
    ],
)
def test_models_are_pydantic_v2(model_cls: type) -> None:
    """Constitution Principle I: every model is a frozen Pydantic v2 BaseModel."""
    assert issubclass(model_cls, BaseModel), (
        f"{model_cls.__name__} must be a Pydantic BaseModel"
    )
    assert model_cls.model_config.get("frozen") is True, (
        f"{model_cls.__name__} must be frozen (immutable)"
    )


# --- Metric validation ---

def test_metric_name_must_be_snake_case() -> None:
    """FR-003: metric names are snake_case so they can be referenced by id."""
    with pytest.raises(Exception):
        Metric(
            name="Gross Sales",  # invalid — capital + space
            business_description="x",
            formula_sql='SUM("Sales")',
            source_table="Orders",
            aggregation="SUM",
        )


def test_all_canonical_metrics_present() -> None:
    """FR-003: exactly 8 canonical metrics defined in metrics.py."""
    names = {m.name for m in get_metrics()}
    expected = {
        "gross_sales",
        "returned_amount",
        "net_sales",
        "return_rate",
        "total_profit",
        "net_profit",
        "avg_order_value",
        "order_count",
    }
    assert names == expected


# --- SemanticViewer semantics ---

def test_viewer_accepts_empty_regions() -> None:
    """FR-014: a viewer with no regions is valid (sees 0 rows at query time)."""
    viewer = SemanticViewer(viewer_id="nobody", regions=[], allows_full_access=False)
    assert viewer.regions == []
    assert viewer.allows_full_access is False


def test_viewer_id_must_be_snake_case() -> None:
    with pytest.raises(Exception):
        SemanticViewer(viewer_id="Alice", regions=["Caribbean"])  # capital


# --- SemanticLayerDocument should build with canonical metrics ---

def _build_canonical_document() -> SemanticLayerDocument:
    """Construct a document matching the canonical 8 metrics + 11 dimensions + 2 relationships."""
    metrics = get_metrics()
    # Build a dimension list — column names match the dictionary fixture.
    dims = [
        Dimension(
            name="region",
            column="Region",
            source_table="Orders",
            business_description="Geographic region of the order.",
            dimension_type="geographic",
        ),
        Dimension(
            name="country",
            column="Country",
            source_table="Orders",
            business_description="Country of the delivery address.",
            dimension_type="geographic",
        ),
        Dimension(
            name="market",
            column="Market",
            source_table="Orders",
            business_description="Top-level market.",
            dimension_type="geographic",
        ),
        Dimension(
            name="segment",
            column="Segment",
            source_table="Orders",
            business_description="Customer segment.",
            dimension_type="categorical",
        ),
        Dimension(
            name="category",
            column="Category",
            source_table="Orders",
            business_description="Product category.",
            dimension_type="categorical",
        ),
        Dimension(
            name="sub_category",
            column="Sub-Category",
            source_table="Orders",
            business_description="Product sub-category.",
            dimension_type="categorical",
        ),
        Dimension(
            name="ship_mode",
            column="Ship Mode",
            source_table="Orders",
            business_description="Shipping class.",
            dimension_type="categorical",
        ),
        Dimension(
            name="order_priority",
            column="Order Priority",
            source_table="Orders",
            business_description="Priority level of the order.",
            dimension_type="categorical",
        ),
        Dimension(
            name="order_date",
            column="Order Date",
            source_table="Orders",
            business_description="Date the order was placed.",
            dimension_type="temporal",
        ),
        Dimension(
            name="customer",
            column="Customer Name",
            source_table="Orders",
            business_description="Customer name.",
            dimension_type="categorical",
        ),
        Dimension(
            name="product",
            column="Product Name",
            source_table="Orders",
            business_description="Product name.",
            dimension_type="categorical",
        ),
    ]
    rels = [
        SemanticRelationship(
            name="orders_to_returns",
            from_table="Returns",
            from_column="Order ID",
            to_table="Orders",
            to_column="Order ID",
            cardinality="N:1",
            join_type="LEFT",
            notes='Returns."Order ID" has 63 duplicates — use EXISTS, not direct JOIN.',
        ),
        SemanticRelationship(
            name="orders_to_people_by_region",
            from_table="People",
            from_column="Region",
            to_table="Orders",
            to_column="Region",
            cardinality="1:N",
            join_type="INNER",
            notes="Taxonomy mismatch: People splits Canada (Eastern/Western) vs Orders' single Canada.",
        ),
    ]
    tables = [
        TableSemanticClassification(
            name="Orders", table_type="fact",
            purpose="Transactional fact table (one row per order line).",
        ),
        TableSemanticClassification(
            name="Returns", table_type="fact",
            purpose="Returned order lines (for net sales logic).",
        ),
        TableSemanticClassification(
            name="People", table_type="governance_mapping",
            purpose="Region-to-person mapping (viewer RLS anchor).",
        ),
    ]
    return SemanticLayerDocument(
        version="1.0.0",
        tables=tables,
        metrics=metrics,
        dimensions=dims,
        relationships=rels,
        source_sha256="abc",
        semantic_source_sha256="def",
        generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        assumptions=[
            "net_profit proportionally discounts total_profit by the returned/gross sales ratio.",
        ],
    )


def test_canonical_document_builds() -> None:
    """Sanity: the canonical content satisfies the document schema."""
    doc = _build_canonical_document()
    assert len(doc.metrics) == 8
    assert len(doc.dimensions) >= 11
    assert len(doc.relationships) == 2
    assert len(doc.tables) == 3
    # Provenance hashes recorded.
    assert doc.source_sha256 and doc.semantic_source_sha256


# --- Resolver Protocol is runtime_checkable ---

def test_resolver_protocol_is_runtime_checkable() -> None:
    """SemanticQueryResolverProtocol is runtime_checkable (constitution Principle I)."""
    # A minimal fake that structurally satisfies the Protocol.
    class _FakeResolver:
        def apply_rls(self, sql: str, viewer: SemanticViewer, table_def: object) -> str:
            return sql

    assert isinstance(_FakeResolver(), SemanticQueryResolverProtocol)


def test_resolver_protocol_rejects_missing_apply_rls() -> None:
    """A class without `apply_rls` is NOT a resolver."""
    class _MissingMethod:
        def some_other_method(self) -> None:
            ...

    assert not isinstance(_MissingMethod(), SemanticQueryResolverProtocol)
