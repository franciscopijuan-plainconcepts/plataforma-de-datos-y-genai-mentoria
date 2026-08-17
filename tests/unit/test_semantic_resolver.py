"""Unit tests for the SemanticQueryResolver (v2.0).

Pure-function tests of `apply_rls` — no DB, no LLM. Covers all cases from
research.md Part A's edge table, plus SQL injection defensiveness.

Reference: specs/003-semantic-layer-v1/tasks.md T018
            specs/003-semantic-layer-v1/research.md Part A (RLS Strategy).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.contracts.data_access import ColumnDef, LogicalType, TableDef
from src.contracts.semantic_layer import SemanticViewer
from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver


# --- Fixtures -------------------------------------------------------------


def _orders_table_def() -> TableDef:
    """Minimal Orders TableDef — the resolver only needs the schema for sanity."""
    return TableDef(
        name="Orders",
        columns=[
            ColumnDef(name="Region", logical_type=LogicalType.STRING, max_length=50, nullable=False),
            ColumnDef(name="Sales", logical_type=LogicalType.DECIMAL, precision=12, scale=4, nullable=False),
        ],
        description="Orders table.",
    )


def _viewer(regions: list[str], allows_full_access: bool = False) -> SemanticViewer:
    return SemanticViewer(
        viewer_id="test_viewer",
        regions=regions,
        allows_full_access=allows_full_access,
        is_local_dev=True,  # local-dev by default in tests
    )


# --- Basic wrapping behavior ---------------------------------------------


def test_single_region_wraps_with_in_clause() -> None:
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean"])
    sql = 'SELECT SUM("Sales") FROM Orders'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # The wrapping should produce a SELECT * FROM (...) AS _gov WHERE "Region" IN ('Caribbean')
    assert result.startswith("SELECT * FROM (")
    assert result.endswith('WHERE "Region" IN (\'Caribbean\')')
    # The original SQL should be inside the subquery.
    assert 'SELECT SUM("Sales") FROM Orders' in result
    assert "_gov AS " not in result  # alias is OUTSIDE the subquery


def test_multiple_regions_join_with_commas() -> None:
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean", "Central America"])
    sql = 'SELECT * FROM Orders'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    assert result == (
        'SELECT * FROM (SELECT * FROM Orders) AS _gov '
        'WHERE "Region" IN (\'Caribbean\', \'Central America\')'
    )


# --- Empty regions => WHERE FALSE (viewer sees 0 rows) -------------------


def test_empty_regions_returns_where_false() -> None:
    """FR-014 / SC-003: a viewer with no regions sees 0 rows."""
    resolver = SemanticQueryResolver()
    viewer = _viewer([])
    sql = 'SELECT SUM("Sales") FROM Orders'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    assert result.endswith("WHERE FALSE")
    assert "SELECT * FROM (" in result
    assert 'SELECT SUM("Sales") FROM Orders' in result


# --- allows_full_access (only effective in local-dev) -------------------


def test_full_access_in_local_dev_returns_unchanged_and_logs() -> None:
    """FR-013: allows_full_access in local-dev bypasses RLS + logs a gov.bypass event."""
    log_calls: list[tuple[str, str, list[str]]] = []

    def capture(viewer_id: str, sql: str, regions: list[str]) -> None:
        log_calls.append((viewer_id, sql, list(regions)))

    resolver = SemanticQueryResolver(gov_bypass_logger=capture)
    viewer = SemanticViewer(
        viewer_id="admin_dev",
        regions=[],
        allows_full_access=True,
        is_local_dev=True,
    )
    sql = 'SELECT SUM("Sales") FROM Orders'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    assert result == sql  # unchanged
    assert len(log_calls) == 1
    assert log_calls[0][0] == "admin_dev"
    assert log_calls[0][1] == sql


def test_full_access_in_non_local_environment_falls_back_to_where_false() -> None:
    """Defense-in-depth: even with allows_full_access=True, non-local env enforces RLS."""
    resolver = SemanticQueryResolver()
    viewer = SemanticViewer(
        viewer_id="admin_prod",
        regions=[],  # no regions + allows_full_access=True but NOT local-dev
        allows_full_access=True,
        is_local_dev=False,
    )
    sql = 'SELECT SUM("Sales") FROM Orders'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # Should be forced to WHERE FALSE (no regions, no bypass allowed).
    assert result.endswith("WHERE FALSE")


# --- Preserve existing WHERE / GROUP BY / LIMIT -------------------------


def test_existing_where_is_preserved_inside_subquery() -> None:
    """The wrapping must NOT mangle an existing WHERE inside the original SQL."""
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean"])
    sql = 'SELECT * FROM Orders WHERE "Region" = \'Central US\''
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # The original WHERE is inside the subquery; the outer WHERE narrows it.
    assert 'WHERE "Region" = \'Central US\'' in result  # original WHERE intact
    assert result.endswith('WHERE "Region" IN (\'Caribbean\')')  # outer filter added


def test_group_by_is_preserved_inside_subquery() -> None:
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean"])
    sql = (
        'SELECT "Region", SUM("Sales") FROM Orders '
        'GROUP BY "Region" ORDER BY "Region"'
    )
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # GROUP BY should still be inside the subquery.
    assert 'GROUP BY "Region"' in result
    assert "ORDER BY" in result


def test_limit_is_preserved_inside_subquery() -> None:
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean"])
    sql = 'SELECT * FROM Orders LIMIT 10'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # LIMIT should be inside the subquery.
    assert "LIMIT 10" in result


def test_join_to_returns_is_preserved_inside_subquery() -> None:
    """US2 contract: a SQL with JOIN to Returns must still be wrappable."""
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean"])
    sql = (
        'SELECT o."Region", SUM(o."Sales") AS net '
        "FROM Orders o "
        'WHERE NOT EXISTS (SELECT 1 FROM Returns r WHERE r."Order ID" = o."Order ID") '
        'GROUP BY o."Region"'
    )
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # The EXISTS subquery should be intact inside the wrapping.
    assert "EXISTS" in result
    assert "FROM Returns r" in result


# --- Trailing semicolon handling -----------------------------------------


def test_trailing_semicolon_is_stripped_in_wrap() -> None:
    """A validated SQL may end with `;` (SqlValidator allows optional trailing
    semicolon); the wrapper strips it to produce valid subquery syntax."""
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean"])
    sql = 'SELECT * FROM Orders;'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # The internal subquery should NOT have the trailing semicolon.
    # The whole result should end with the WHERE clause (no trailing ;).
    assert not result.endswith(";")
    assert result.endswith("('Caribbean')")


# --- SQL injection defense on `regions` ---------------------------------


def test_region_with_single_quote_is_escaped() -> None:
    """A region name containing a single quote must be escaped (defensive)."""
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Test'Region"])
    sql = 'SELECT * FROM Orders'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # The single quote should be doubled ('' escape).
    assert "'Test''Region'" in result


def test_region_with_semicolon_cannot_inject() -> None:
    """A region value containing a `;` cannot break out of the IN clause (single-quote
    string literal escape). The injected `;` stays INSIDE the single-quoted
    string literal, so it cannot terminate the SELECT statement early."""
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean; DROP TABLE x"])
    sql = 'SELECT * FROM Orders'
    result = resolver.apply_rls(sql, viewer, _orders_table_def())
    # The injected `;` must be inside the single-quoted literal, not a separator.
    assert "'Caribbean; DROP TABLE x'" in result
    # No trailing semicolon escaping issue (the wrap ends with the Closing paren).
    assert result.endswith("')")
    # No SECOND statement was introduced — the wrap is a single SELECT.
    # Count occurrences of 'SELECT' outside of subquery: should be exactly 1 (the
    # outer wrapper), not 2 (which would indicate injected SQL).
    # The inner SQL also starts with SELECT, so total SELECTs = 2.
    assert result.count("SELECT") == 2  # outer wrapper + inner query


# --- Edge cases ----------------------------------------------------------


def test_resolver_is_protocol_instance() -> None:
    """SemanticQueryResolver implements SemanticQueryResolverProtocol."""
    from src.contracts.semantic_layer import SemanticQueryResolverProtocol

    resolver = SemanticQueryResolver()
    assert isinstance(resolver, SemanticQueryResolverProtocol)


def test_resolver_is_pure_no_outputs_to_db() -> None:
    """Same input => same output (referential transparency — pure function)."""
    resolver = SemanticQueryResolver()
    viewer = _viewer(["Caribbean", "Central America"])
    sql = 'SELECT * FROM Orders'
    r1 = resolver.apply_rls(sql, viewer, _orders_table_def())
    r2 = resolver.apply_rls(sql, viewer, _orders_table_def())
    assert r1 == r2
