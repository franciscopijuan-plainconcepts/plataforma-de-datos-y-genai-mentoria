"""Deterministic arithmetic reference using connection-local PostgreSQL tables."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path

from dotenv import load_dotenv
import pytest
import sqlglot

from src.data_access.adapters.postgres.connection import (
    DictConnection,
    PostgresConfig,
    connect,
)
from src.contracts.data_access import TableDef
from src.data_access.adapters.postgres.repository import PostgresRepository
from tests.semantic_fixtures import (
    _build_test_table_defs, _load_legacy_semantic_layer, _load_test_semantic_layer,
)


@dataclass(frozen=True)
class ReferenceLine:
    order_id: str
    region: str
    sales: Decimal
    profit: Decimal


LINES = (
    ReferenceLine("A", "North", Decimal("100"), Decimal("20")),
    ReferenceLine("A", "North", Decimal("50"), Decimal("-5")),
    ReferenceLine("B", "South", Decimal("150"), Decimal("30")),
    ReferenceLine("C", "South", Decimal("0"), Decimal("-10")),
)
RETURNED = frozenset({"A"})
DIMENSIONS = (
    "Region", "Country", "Market", "Segment", "Category", "Sub-Category",
    "Ship Mode", "Order Priority", "Order Date", "Customer Name", "Product Name",
)


@pytest.fixture()
def reference_connection() -> Iterator[DictConnection]:
    if os.environ.get("RUN_SEMANTIC_PG_TESTS") != "1":
        pytest.skip("Set RUN_SEMANTIC_PG_TESTS=1 for isolated PostgreSQL references")
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    with connect(PostgresConfig.from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL search_path TO pg_temp, pg_catalog")
            cur.execute(
                'CREATE TEMP TABLE "Orders" ('
                '"Row ID" integer PRIMARY KEY, "Order ID" text, '
                '"Sales" numeric(12,4), "Profit" numeric(12,4), '
                '"Region" text, "Country" text, "Market" text, "Segment" text, '
                '"Category" text, "Sub-Category" text, "Ship Mode" text, '
                '"Order Priority" text, "Order Date" timestamp, '
                '"Customer Name" text, "Product Name" text) ON COMMIT DROP'
            )
            cur.execute(
                'CREATE TEMP TABLE "Returns" ('
                '"Return ID" integer PRIMARY KEY, "Order ID" text, "Region" text)'
                " ON COMMIT DROP"
            )
            for index, line in enumerate(LINES, start=1):
                cur.execute(
                    'INSERT INTO "Orders" VALUES '
                    "(%s,%s,%s,%s,%s,'Country','Market','Segment','Category',"
                    "'Subcategory','Ship','Priority','2026-01-01','Customer','Product')",
                    (index, line.order_id, line.sales, line.profit, line.region),
                )
            cur.execute(
                'INSERT INTO "Returns" VALUES (1,\'A\',\'North\'),(2,\'A\',\'North\')'
            )
        try:
            yield conn
        finally:
            conn.rollback()


def expected_metrics(lines: tuple[ReferenceLine, ...]) -> dict[str, Decimal | int | None]:
    count = len({line.order_id for line in lines})
    if not lines:
        return {
            "gross_sales": None, "returned_amount": None, "net_sales": None,
            "return_rate": None, "total_profit": None, "net_profit": None,
            "avg_order_value": None, "order_count": 0,
        }
    gross = sum((line.sales for line in lines), Decimal(0))
    returned = sum((line.sales for line in lines if line.order_id in RETURNED), Decimal(0))
    profit = sum((line.profit for line in lines), Decimal(0))
    return {
        "gross_sales": gross,
        "returned_amount": returned,
        "net_sales": gross - returned,
        "return_rate": returned / gross if gross else None,
        "total_profit": profit,
        "net_profit": profit - (returned / gross * profit if gross else Decimal(0)),
        "avg_order_value": gross / count if count else None,
        "order_count": count,
    }


@pytest.mark.parametrize("region", [None, "North", "South", "Missing"])
def test_metric_reference(
    reference_connection: DictConnection, region: str | None,
) -> None:
    selected = tuple(line for line in LINES if region is None or line.region == region)
    expected = expected_metrics(selected)
    for metric in _load_test_semantic_layer().metrics:
        # Metadata uses bare mixed-case tables; quote identifiers as the prompt requires.
        sql = sqlglot.parse_one(
            f'SELECT {metric.formula_sql} AS value FROM "Orders"', read="postgres"
        ).sql(dialect="postgres", identify=True)
        if region is not None:
            sql += ' WHERE "Region" = %s'
        with reference_connection.cursor() as cur:
            cur.execute(sql, (region,) if region is not None else None)
            row = cur.fetchone()
        assert row is not None
        actual = row["value"]
        wanted = expected[metric.name]
        if wanted is None:
            assert actual is None, metric.name
        elif metric.name == "order_count":
            assert type(actual) is int and actual == wanted
        else:
            assert isinstance(actual, Decimal), metric.name
            assert isinstance(wanted, Decimal)
            if metric.name in {"return_rate", "avg_order_value", "net_profit"}:
                assert abs(actual - wanted) <= Decimal("1e-12"), metric.name
            else:
                assert actual == wanted, metric.name


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_dimensions_preserve_grain(
    reference_connection: DictConnection, dimension: str,
) -> None:
    from psycopg.sql import Identifier, SQL

    with reference_connection.cursor() as cur:
        cur.execute(
            SQL('SELECT {}, SUM("Sales") AS total, COUNT(*) AS lines '
                'FROM "Orders" GROUP BY {}').format(Identifier(dimension), Identifier(dimension))
        )
        rows = cur.fetchall()
    totals: list[Decimal] = []
    counts: list[int] = []
    for row in rows:
        total, count = row["total"], row["lines"]
        assert isinstance(total, Decimal)
        assert isinstance(count, int)
        totals.append(total)
        counts.append(count)
    assert sum(totals) == Decimal("300")
    assert sum(counts) == 4


def test_zero_sales(reference_connection: DictConnection) -> None:
    with reference_connection.cursor() as cur:
        cur.execute('UPDATE "Orders" SET "Sales" = 0')
    for metric in _load_test_semantic_layer().metrics:
        if metric.name not in {"return_rate", "avg_order_value", "net_profit"}:
            continue
        sql = sqlglot.parse_one(
            f'SELECT {metric.formula_sql} AS value FROM "Orders"', read="postgres"
        ).sql(dialect="postgres", identify=True)
        with reference_connection.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
        assert row is not None
        assert row["value"] == {"return_rate": None, "avg_order_value": Decimal(0),
                                "net_profit": Decimal("35")}[metric.name]


def test_legacy_null_projection(reference_connection: DictConnection) -> None:
    """Characterize the existing public shape, not a desired new null policy."""
    repo = PostgresRepository(conn=reference_connection)
    rows = repo.execute_readonly_query(
        'SELECT SUM("Sales") AS total FROM "Orders" WHERE FALSE',
        TableDef(name="Orders", columns=[], description="Temporary reference"),
    )
    assert len(rows) == 1
    assert rows[0].data == {}


@pytest.mark.parametrize("regions", [["North"], ["South"], ["North", "South"], []])
def test_governed_metrics(reference_connection: DictConnection, regions: list[str]) -> None:
    from src.contracts.semantic_layer import SemanticViewer
    from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver

    viewer = SemanticViewer(viewer_id="reference", regions=regions)
    table = _build_test_table_defs()[0]
    expected = expected_metrics(tuple(line for line in LINES if line.region in regions))
    for metric in _load_test_semantic_layer().metrics:
        generated = sqlglot.parse_one(
            f'SELECT {metric.formula_sql} AS value FROM "Orders"', read="postgres"
        ).sql(dialect="postgres", identify=True)
        effective = SemanticQueryResolver().apply_rls(generated, viewer, table)
        with reference_connection.cursor() as cur:
            cur.execute(effective)
            rows = cur.fetchall()
        if not regions:
            assert rows == []
            continue
        assert len(rows) == 1 and set(rows[0]) == {"value"}
        actual, wanted = rows[0]["value"], expected[metric.name]
        if wanted is None:
            assert actual is None
        elif metric.name == "order_count":
            assert type(actual) is int and actual == wanted
        else:
            assert isinstance(actual, Decimal) and isinstance(wanted, Decimal)
            tolerance = Decimal("1e-12") if metric.name in {
                "return_rate", "avg_order_value", "net_profit",
            } else Decimal(0)
            assert abs(actual - wanted) <= tolerance


@pytest.mark.parametrize("sql,north,south", [
    ('SELECT SUM("Sales") AS value FROM "Orders" WHERE "Region" = \'South\' OR "Sales" > 0',
     Decimal("150"), Decimal("150")),
    ('SELECT SUM(o."Sales") AS value FROM "Orders" o WHERE EXISTS '
     '(SELECT 1 FROM "Returns" r WHERE r."Order ID" = o."Order ID")',
     Decimal("150"), None),
    ('SELECT SUM(o."Sales") AS value FROM "Orders" o WHERE NOT EXISTS '
     '(SELECT 1 FROM "Returns" r WHERE r."Order ID" = o."Order ID")',
     None, Decimal("150")),
    ('SELECT o."Sales" AS value FROM "Orders" o ORDER BY o."Profit" DESC LIMIT 1',
     Decimal("100"), Decimal("150")),
    ('SELECT SUM(o."Sales") AS value FROM "Orders" o JOIN "Returns" r '
     'ON r."Order ID" = o."Order ID" WHERE o."Sales" > 0 OR r."Region" = \'South\'',
     Decimal("300"), None),
])
def test_governed_query_scopes(
    reference_connection: DictConnection, sql: str, north: Decimal | None, south: Decimal | None,
) -> None:
    from src.contracts.semantic_layer import SemanticViewer
    from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver

    for region, expected in [("North", north), ("South", south)]:
        effective = SemanticQueryResolver().apply_rls(
            sql, SemanticViewer(viewer_id=region.lower(), regions=[region]), _build_test_table_defs()[0],
        )
        with reference_connection.cursor() as cur:
            cur.execute(effective)
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0] == {"value": expected}
        # A published statement is a static snapshot: reexecution is identical.
        with reference_connection.cursor() as cur:
            cur.execute(effective)
            assert cur.fetchall() == rows


def test_return_exists_preserves_order_link_with_different_region(
    reference_connection: DictConnection,
) -> None:
    from src.contracts.semantic_layer import SemanticViewer
    from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver

    with reference_connection.cursor() as cur:
        cur.execute('UPDATE "Returns" SET "Region" = \'Other regional label\'')
    for metric in _load_test_semantic_layer().metrics:
        generated = sqlglot.parse_one(
            f'SELECT {metric.formula_sql} AS value FROM "Orders"', read="postgres"
        ).sql(dialect="postgres", identify=True)
        effective = SemanticQueryResolver().apply_rls(
            generated, SemanticViewer(viewer_id="north", regions=["North"]),
            _build_test_table_defs()[0],
        )
        with reference_connection.cursor() as cur:
            cur.execute(effective)
            row = cur.fetchone()
        assert row is not None
        expected = expected_metrics(tuple(line for line in LINES if line.region == "North"))[metric.name]
        assert row["value"] == expected


@pytest.mark.parametrize("dimension", [None, *DIMENSIONS])
def test_legacy_ossie_result_equivalence(
    reference_connection: DictConnection, dimension: str | None,
) -> None:
    legacy = _load_legacy_semantic_layer()
    restored = _load_test_semantic_layer()
    by_name = {metric.name: metric for metric in restored.metrics}
    assert set(by_name) == {metric.name for metric in legacy.metrics}
    for region in [None, "North", "South", "Missing"]:
        for metric in legacy.metrics:
            results: list[list[dict[str, object]]] = []
            for formula in [metric.formula_sql, by_name[metric.name].formula_sql]:
                projection = f'"{dimension}", ' if dimension else ""
                sql = f'SELECT {projection}{formula} AS value FROM "Orders"'
                if region is not None:
                    sql += ' WHERE "Region" = %s'
                if dimension:
                    sql += f' GROUP BY "{dimension}" ORDER BY "{dimension}"'
                sql = sqlglot.parse_one(sql, read="postgres").sql(dialect="postgres", identify=True)
                with reference_connection.cursor() as cur:
                    cur.execute(sql, (region,) if region is not None else None)
                    results.append(cur.fetchall())
            assert len(results[0]) == len(results[1])
            for before, after in zip(*results, strict=True):
                assert before.keys() == after.keys()
                for key in before:
                    assert type(before[key]) is type(after[key])
                    if isinstance(before[key], Decimal) and metric.name in {
                        "return_rate", "avg_order_value", "net_profit",
                    }:
                        assert isinstance(after[key], Decimal)
                        assert abs(before[key] - after[key]) <= Decimal("1e-12")
                    else:
                        assert before[key] == after[key]
