"""Canonical metric definitions for the Semantic Layer v2.0.

Hard-coded (no runtime metric DSL). The builder consumes this list to
construct the `SemanticLayerDocument.metrics` field. Adding a new metric
amounts to: (1) add an entry here, (2) regenerate the artifact via CLI.

Each metric references only columns that exist in the warehouse schema
(Orders / Returns). The builder validates this at build-time against the
`DataDictionaryDocument` and fails fast if a referenced column is missing.

Reference: specs/003-semantic-layer-v1/research.md Part F (canonized formulas).
            specs/003-semantic-layer-v1/data-model.md § Canonical metrics.
            specs/003-semantic-layer-v1/contracts/semantic_layer.md.
"""

from __future__ import annotations

from src.contracts.semantic_layer import Metric


# ---------------------------------------------------------------------------
# The 8 canonical metrics (FR-003).
# ---------------------------------------------------------------------------
# Notes:
# - `returned_amount`, `net_sales`, `return_rate`, `net_profit` all use
#   `EXISTS (SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID")`
#   to detect returned lines WITHOUT duplicating Orders rows (the 63 duplicate
#   Order ID values in Returns means a direct JOIN would inflate row counts).
# - `net_profit` assumes proportionality between Sales and Profit for returned
#   lines (documented as assumption). Alternative subquery-based formula is
#   documented in research.md Part F for v3.0+.
# - All identifiers are double-quoted to match the PostgreSQL case-sensitive
#   schema (title-case columns created by the loader).
#   `formula_sql` is metadata; the SQL validator does NOT parse it (it only
#   validates SQL the LLM generates for execution). The `uses_returns` flag is
#   what the builder checks against the relationship Orders-Returns existence.

_METRICS: list[Metric] = [
    Metric(
        name="gross_sales",
        business_description=(
            "Gross sales revenue across all order lines, before subtracting "
            "any returned revenue."
        ),
        formula_sql='SUM("Sales")',
        source_table="Orders",
        aggregation="SUM",
        derives_from=[],
        uses_returns=False,
        assumption=None,
    ),
    Metric(
        name="returned_amount",
        business_description=(
            "Total sales revenue of order lines that were returned (have a "
            "matching entry in the Returns table by Order ID)."
        ),
        formula_sql=(
            'SUM(CASE WHEN EXISTS (SELECT 1 FROM Returns '
            'WHERE Returns."Order ID" = Orders."Order ID") '
            'THEN "Sales" ELSE 0 END)'
        ),
        source_table="Orders",
        aggregation="EXPRESSION",
        derives_from=[],
        uses_returns=True,
        assumption=(
            "A line is considered returned if at least one Returns row matches "
            'its Order ID. Because Returns."Order ID" has duplicates (multi-line '
            "returns), EXISTS is used instead of JOIN to avoid inflating rows."
        ),
    ),
    Metric(
        name="net_sales",
        business_description=(
            "Net sales revenue: gross sales minus returned order-line revenue."
        ),
        formula_sql=(
            'SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM Returns '
            'WHERE Returns."Order ID" = Orders."Order ID") '
            'THEN "Sales" ELSE 0 END)'
        ),
        source_table="Orders",
        aggregation="EXPRESSION",
        derives_from=["gross_sales", "returned_amount"],
        uses_returns=True,
        assumption=None,
    ),
    Metric(
        name="return_rate",
        business_description=(
            "Fraction of gross sales that was returned: returned_amount / gross_sales."
        ),
        formula_sql=(
            'CASE WHEN SUM("Sales") = 0 THEN NULL '
            "ELSE SUM(CASE WHEN EXISTS (SELECT 1 FROM Returns "
            'WHERE Returns."Order ID" = Orders."Order ID") '
            'THEN "Sales" ELSE 0 END) / SUM("Sales") END'
        ),
        source_table="Orders",
        aggregation="RATIO",
        derives_from=["returned_amount", "gross_sales"],
        uses_returns=True,
        assumption=None,
    ),
    Metric(
        name="total_profit",
        business_description=(
            "Sum of signed profit across all order lines (negatives are losses)."
        ),
        formula_sql='SUM("Profit")',
        source_table="Orders",
        aggregation="SUM",
        derives_from=[],
        uses_returns=False,
        assumption=None,
    ),
    Metric(
        name="net_profit",
        business_description=(
            "Net profit: total profit minus the proportion of profit corresponding "
            "to returned order lines."
        ),
        formula_sql=(
            'SUM("Profit") - (CASE WHEN SUM("Sales") = 0 THEN 0 '
            "ELSE (SUM(CASE WHEN EXISTS (SELECT 1 FROM Returns "
            'WHERE Returns."Order ID" = Orders."Order ID") '
            'THEN "Sales" ELSE 0 END) / SUM("Sales")) * SUM("Profit") END)'
        ),
        source_table="Orders",
        aggregation="EXPRESSION",
        derives_from=["total_profit", "returned_amount"],
        uses_returns=True,
        assumption=(
            "Proportionally discounts total_profit by the fraction of sales that "
            "was returned (returned_amount / gross_sales * total_profit). Assumes "
            "returned lines have no independent profit record; a more precise "
            "subquery-based formula is documented for v3.0+."
        ),
    ),
    Metric(
        name="avg_order_value",
        business_description=(
            "Average value of a distinct order: gross sales divided by the number "
            "of distinct Order IDs."
        ),
        formula_sql='SUM("Sales") / NULLIF(COUNT(DISTINCT "Order ID"), 0)',
        source_table="Orders",
        aggregation="EXPRESSION",
        derives_from=["gross_sales"],
        uses_returns=False,
        assumption=None,
    ),
    Metric(
        name="order_count",
        business_description="Number of distinct orders (by Order ID).",
        formula_sql='COUNT(DISTINCT "Order ID")',
        source_table="Orders",
        aggregation="COUNT_DISTINCT",
        derives_from=[],
        uses_returns=False,
        assumption=None,
    ),
]


def get_metrics() -> list[Metric]:
    """Return the canonical list of Semantic Layer metrics (FR-003)."""
    return list(_METRICS)


def get_metric_names() -> list[str]:
    """Return the names of all canonical metrics, in declared order."""
    return [m.name for m in _METRICS]


__all__ = ["get_metrics", "get_metric_names"]
