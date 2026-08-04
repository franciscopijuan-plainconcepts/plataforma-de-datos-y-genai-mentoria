"""Curated Kaggle semantic descriptions for the Data Dictionary.

Provides the `KaggleSemanticSource` containing:
- per-table Kaggle labels (Orders: Transactional Logs; Returns: Reverse
  Logistics; People: Sales Governance) and business purposes, and
- per-column business descriptions + key-kind flags (primary/foreign).

This is the semantic half of the dictionary; the EDA-derived types populate
the other half (see `generator.py`). All content here is curated from the
Kaggle dataset documentation and data-model.md.

Reference: specs/001-data-genai-platform-baseline/contracts/dictionary.md
            specs/001-data-genai-platform-baseline/data-model.md
            specs/001-data-genai-platform-baseline/research.md Part A.4
"""

from __future__ import annotations

from src.contracts.dictionary import (
    ColumnSemantic,
    KaggleSemanticSource,
    TableSemantic,
)


# ---------------------------------------------------------------------------
# Per-column business descriptions
# (curated from Kaggle dataset documentation + data-model.md)
# ---------------------------------------------------------------------------

_ORDERS_COLUMNS: list[ColumnSemantic] = [
    ColumnSemantic(name="Row ID", business_description="Unique identifier for each order line (one row per line item within an order).", is_key=True, key_kind="primary"),
    ColumnSemantic(name="Order ID", business_description="Identifier for the order; one order spans multiple line items, so this is not unique per row.", is_key=False, key_kind=None),
    ColumnSemantic(name="Order Date", business_description="Date the order was placed.", is_key=False, key_kind=None),
    ColumnSemantic(name="Ship Date", business_description="Date the order was shipped (always on or after Order Date).", is_key=False, key_kind=None),
    ColumnSemantic(name="Ship Mode", business_description="Shipping class selected for the order.", is_key=False, key_kind=None),
    ColumnSemantic(name="Customer ID", business_description="Unique identifier for the customer who placed the order.", is_key=False, key_kind=None),
    ColumnSemantic(name="Customer Name", business_description="Name of the customer who placed the order.", is_key=False, key_kind=None),
    ColumnSemantic(name="Segment", business_description="Customer segment (consumer, corporate, or home office).", is_key=False, key_kind=None),
    ColumnSemantic(name="Postal Code", business_description="Postal/ZIP code of the delivery address. Only populated for US/Canada rows (80% NULL).", is_key=False, key_kind=None),
    ColumnSemantic(name="City", business_description="City of the delivery address.", is_key=False, key_kind=None),
    ColumnSemantic(name="State", business_description="State/province of the delivery address.", is_key=False, key_kind=None),
    ColumnSemantic(name="Country", business_description="Country of the delivery address.", is_key=False, key_kind=None),
    ColumnSemantic(name="Region", business_description="Geographic region of the order. Shared with Returns and People — the basis for future v2.0 Row-Level Security.", is_key=True, key_kind="foreign"),
    ColumnSemantic(name="Market", business_description="Top-level market the order belongs to (Asia Pacific, Europe, Africa, LATAM, USCA).", is_key=False, key_kind=None),
    ColumnSemantic(name="Product ID", business_description="Unique identifier for the product ordered.", is_key=False, key_kind=None),
    ColumnSemantic(name="Product Name", business_description="Human-readable product name.", is_key=False, key_kind=None),
    ColumnSemantic(name="Sub-Category", business_description="Product sub-category (e.g., Binders, Chairs, Phones).", is_key=False, key_kind=None),
    ColumnSemantic(name="Category", business_description="Top-level product category (Furniture, Office Supplies, Technology).", is_key=False, key_kind=None),
    ColumnSemantic(name="Sales", business_description="Gross sales revenue for the line item, before returns. Ingredient for v2.0 gross/net sales logic.", is_key=False, key_kind=None),
    ColumnSemantic(name="Quantity", business_description="Number of units ordered for the line item.", is_key=False, key_kind=None),
    ColumnSemantic(name="Discount", business_description="Fractional discount applied to the line item (0.0–0.85). Ingredient for net-vs-gross computation in v2.0.", is_key=False, key_kind=None),
    ColumnSemantic(name="Profit", business_description="Profit for the line item; signed (negative values represent losses). Ingredient for v2.0 business logic.", is_key=False, key_kind=None),
    ColumnSemantic(name="Shipping Cost", business_description="Cost to ship the line item.", is_key=False, key_kind=None),
    ColumnSemantic(name="Order Priority", business_description="Priority level of the order (Low, Medium, High, Critical).", is_key=False, key_kind=None),
]

_RETURNS_COLUMNS: list[ColumnSemantic] = [
    ColumnSemantic(name="Return ID", business_description="Surrogate primary key assigned at load (row-ordinal). Introduced because Order ID has duplicates (multi-line returns).", is_key=True, key_kind="primary"),
    ColumnSemantic(name="Returned", business_description="Flag indicating the order was returned. Degenerate: always 'Yes' in the source; the row's presence itself encodes 'returned'.", is_key=False, key_kind=None),
    ColumnSemantic(name="Order ID", business_description="Identifier of the returned order; links to Orders.Order ID. Not unique (multi-line returns produce duplicates).", is_key=True, key_kind="foreign"),
    ColumnSemantic(name="Region", business_description="Geographic region of the returned order. Shared with Orders and People — basis for future v2.0 RLS.", is_key=True, key_kind="foreign"),
]

_PEOPLE_COLUMNS: list[ColumnSemantic] = [
    ColumnSemantic(name="Person", business_description="Name of the regional sales person / manager responsible for a region. Primary key. Normalized at load (non-breaking spaces stripped).", is_key=True, key_kind="primary"),
    ColumnSemantic(name="Region", business_description="Region the person governs. Shared with Orders/Returns — basis for future v2.0 Row-Level Security (a user may only see data for their assigned regions).", is_key=True, key_kind="foreign"),
]


_TABLE_SEMANTICS: dict[str, TableSemantic] = {
    "Orders": TableSemantic(
        name="Orders",
        kaggle_label="Transactional Logs",
        purpose=(
            "The transactional fact table of the Global Superstore Dataset: one row per "
            "order line, capturing sales, discount, profit, customer, product, and regional "
            "attributes. Central to the future v1.0/1.1 Text-to-SQL scope (primary query "
            "surface) and the v2.0 Semantic Layer (gross/net sales)."
        ),
    ),
    "Returns": TableSemantic(
        name="Returns",
        kaggle_label="Reverse Logistics",
        purpose=(
            "Records which orders were returned. Linked to Orders via Order ID. Used in "
            "v2.0 to model business logic distinguishing net vs gross sales (a returned "
            "order's sales are subtracted from gross to compute net)."
        ),
    ),
    "People": TableSemantic(
        name="People",
        kaggle_label="Sales Governance",
        purpose=(
            "Mapping of regional sales people / managers to the regions they govern. The "
            "basis for future v2.0 Row-Level Security: a user may only query data for "
            "regions assigned to them."
        ),
    ),
}

_COLUMN_SEMANTICS: dict[str, list[ColumnSemantic]] = {
    "Orders": _ORDERS_COLUMNS,
    "Returns": _RETURNS_COLUMNS,
    "People": _PEOPLE_COLUMNS,
}


def get_kaggle_semantic_source() -> KaggleSemanticSource:
    """Return the curated Kaggle semantic source for all three tables."""
    return KaggleSemanticSource(table_semantics=_TABLE_SEMANTICS)


def get_column_semantics(table_name: str) -> list[ColumnSemantic]:
    """Return the curated per-column semantics for a table, or [] if unknown."""
    return list(_COLUMN_SEMANTICS.get(table_name, []))


__all__ = ["get_kaggle_semantic_source", "get_column_semantics"]
