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
    result = validate_sql('SELECT * FROM Returns', TABLE_DEF)
    assert not result.accepted
    assert "Returns" in (result.reason or "") or "out-of-scope" in (result.reason or "").lower()


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
