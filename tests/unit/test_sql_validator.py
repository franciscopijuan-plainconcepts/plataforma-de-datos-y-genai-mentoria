"""Unit tests for the SQL validator (no LLM, no DB).

Tests all validation rules:
- SELECT accepted
- INSERT/UPDATE/DELETE/DROP rejected
- Multi-statement rejected
- Comments rejected
- Non-Orders table rejected
- Non-existent column rejected
- Empty SQL rejected

Reference: specs/002-text-to-sql-v1/research.md Part A
            specs/002-text-to-sql-v1/tasks.md T012
"""

from __future__ import annotations

from src.ai_engineering.sql_validator import validate_sql
from src.contracts.data_access import ColumnDef, LogicalType, TableDef


def _make_orders_table_def() -> TableDef:
    """A minimal TableDef for Orders with a few representative columns."""
    return TableDef(
        name="Orders",
        description="Transactional Logs",
        columns=[
            ColumnDef(name="Row ID", logical_type=LogicalType.INTEGER, nullable=False, is_primary_key=True),
            ColumnDef(name="Order ID", logical_type=LogicalType.STRING, max_length=50, nullable=False),
            ColumnDef(name="Sales", logical_type=LogicalType.DECIMAL, precision=12, scale=4, nullable=False),
            ColumnDef(name="Profit", logical_type=LogicalType.DECIMAL, precision=12, scale=4, nullable=False),
            ColumnDef(name="Region", logical_type=LogicalType.STRING, max_length=50, nullable=False),
            ColumnDef(name="Quantity", logical_type=LogicalType.INTEGER, nullable=False),
        ],
    )


TABLE_DEF = _make_orders_table_def()


# --- Accepted queries ---

def test_simple_select_accepted() -> None:
    result = validate_sql('SELECT count(*) FROM Orders', TABLE_DEF)
    assert result.accepted, f"Expected accepted, got: {result.reason}"


def test_select_with_columns_accepted() -> None:
    result = validate_sql('SELECT "Sales", "Profit" FROM Orders WHERE "Quantity" > 5', TABLE_DEF)
    assert result.accepted, f"Expected accepted, got: {result.reason}"


def test_select_with_aggregation_accepted() -> None:
    result = validate_sql(
        'SELECT "Region", SUM("Sales") FROM Orders GROUP BY "Region"', TABLE_DEF
    )
    assert result.accepted, f"Expected accepted, got: {result.reason}"


def test_select_with_order_by_limit_accepted() -> None:
    result = validate_sql(
        'SELECT "Order ID", "Sales" FROM Orders ORDER BY "Sales" DESC LIMIT 5', TABLE_DEF
    )
    assert result.accepted, f"Expected accepted, got: {result.reason}"


def test_select_with_trailing_semicolon_accepted() -> None:
    result = validate_sql('SELECT count(*) FROM Orders;', TABLE_DEF)
    assert result.accepted, f"Expected accepted, got: {result.reason}"


# --- Rejected queries ---

def test_insert_rejected() -> None:
    result = validate_sql('INSERT INTO Orders VALUES (1)', TABLE_DEF)
    assert not result.accepted
    # INSERT is rejected because it doesn't start with SELECT, OR because
    # "insert" is a forbidden keyword. Either reason is valid.
    reason = (result.reason or "").lower()
    assert "insert" in reason or "select" in reason or "forbidden" in reason


def test_update_rejected() -> None:
    result = validate_sql('UPDATE Orders SET "Sales" = 0', TABLE_DEF)
    assert not result.accepted


def test_delete_rejected() -> None:
    result = validate_sql('DELETE FROM Orders', TABLE_DEF)
    assert not result.accepted


def test_drop_rejected() -> None:
    result = validate_sql('DROP TABLE Orders', TABLE_DEF)
    assert not result.accepted


def test_multistatement_rejected() -> None:
    result = validate_sql('SELECT 1; SELECT 2', TABLE_DEF)
    assert not result.accepted
    assert "multiple" in (result.reason or "").lower()


def test_comment_rejected() -> None:
    result = validate_sql('SELECT * FROM Orders -- comment', TABLE_DEF)
    assert not result.accepted
    assert "comment" in (result.reason or "").lower()


def test_non_orders_table_rejected() -> None:
    """v2.0: Returns is now allowed as a JOIN target, but People must be rejected."""
    # Returns references are now accepted (v2.0 net-sales metrics need them).
    # The assertion that Returns is rejected has moved to v1.x behavior; here we
    # assert that People (out-of-scope governance mapping) is still rejected.
    result = validate_sql('SELECT * FROM People', TABLE_DEF)
    assert not result.accepted
    reason = (result.reason or "").lower()
    assert "people" in reason or "out-of-scope" in reason


def test_people_table_rejected() -> None:
    result = validate_sql('SELECT * FROM People', TABLE_DEF)
    assert not result.accepted


def test_nonexistent_column_rejected() -> None:
    result = validate_sql('SELECT nonexistent_col FROM Orders', TABLE_DEF)
    assert not result.accepted
    assert "nonexistent_col" in (result.reason or "")


def test_empty_sql_rejected() -> None:
    result = validate_sql('', TABLE_DEF)
    assert not result.accepted
    assert "empty" in (result.reason or "").lower()


def test_non_select_rejected() -> None:
    result = validate_sql('EXPLAIN SELECT * FROM Orders', TABLE_DEF)
    assert not result.accepted


def test_truncate_rejected() -> None:
    result = validate_sql('TRUNCATE Orders', TABLE_DEF)
    assert not result.accepted


def test_create_rejected() -> None:
    result = validate_sql('CREATE TABLE foo (id int)', TABLE_DEF)
    assert not result.accepted


# --- v2.0 (feature 003): Returns JOIN accepted, People rejected ---

def test_returns_join_accepted() -> None:
    """v2.0: SQL that references Returns (for net-sales-style metrics) is accepted.

    The SqlValidator was extended in feature 003 to allow Returns references
    so the LLM can compute net_sales, returned_amount, return_rate, etc.
    """
    sql = (
        'SELECT o."Region", SUM(o."Sales") AS net '
        "FROM Orders o "
        'WHERE NOT EXISTS (SELECT 1 FROM Returns r WHERE r."Order ID" = o."Order ID") '
        'GROUP BY o."Region"'
    )
    result = validate_sql(sql, TABLE_DEF)
    assert result.accepted, f"Expected accepted, got: {result.reason}"


def test_people_join_rejected() -> None:
    """v2.0: People stays rejected — it is NOT a query surface for the LLM.

    People is the governance mapping (viewer -> regions); it must never appear
    in LLM-generated SQL. The validator rejects it with a clear message.
    """
    sql = (
        'SELECT o."Region", p."Person" '
        'FROM Orders o JOIN People p ON o."Region" = p."Region"'
    )
    result = validate_sql(sql, TABLE_DEF)
    assert not result.accepted
    reason = (result.reason or "").lower()
    assert "people" in reason or "out-of-scope" in reason, (
        f"Expected rejection reason to mention People, got: {result.reason!r}"
    )
