"""PostgreSQL adapter implementing the data-access Protocols.

This is the ONLY engine-specific implementation in the baseline. It
implements `SchemaProvider`, `DataProvider`, and `QueryProvider` (the
reserved Text-to-SQL Protocol carries no methods at this baseline).

Engine-specific rendering (`psycopg.sql` for DDL, `COPY`/batch for bulk
load) is confined to this file (constitution Principle III). Upstream
code MUST NOT import `psycopg` — it depends only on the Protocols in
`src/data_access/interfaces.py` and contract models in
`src/contracts/data_access.py`.

Reference: specs/001-data-genai-platform-baseline/contracts/data_access.md
            specs/001-data-genai-platform-baseline/research.md Part B
"""

from __future__ import annotations

from typing import cast

from psycopg.sql import Composed, SQL, Identifier

from src.contracts.data_access import (
    ColumnDef,
    LoadResult,
    LogicalType,
    OrderRow,
    Row,
    TableDef,
)
from src.contracts.text_to_sql import QueryRow
from src.data_access.adapters.postgres.connection import (
    DictConnection,
    PostgresConfig,
    connect,
)


# --- LogicalType -> PostgreSQL DDL type mapping (engine-specific) ---
# This mapping lives ONLY in this adapter file (never upstream), satisfying
# constitution Principle III.
_LOGICAL_TO_PG: dict[LogicalType, str] = {
    LogicalType.INTEGER: "INTEGER",
    LogicalType.TIMESTAMP: "TIMESTAMP",
    LogicalType.BOOLEAN: "BOOLEAN",
    # DECIMAL(p,s) and STRING(n) are rendered with parameters below.
}


def _render_column_ddl(col: ColumnDef) -> Composed:
    """Render engine-neutral `ColumnDef` to a PostgreSQL column clause.

    Uses `psycopg.sql` for safe identifier/literal composition (see research.md
    Part C "psycopg 3 server-side-binding quirk": DDL cannot be parametrized).
    Returns a `Composed` (the result of `SQL(...).join(...)`).
    """
    # 1. Determine the PG type clause. `.format()` returns `Composed`; raw
    #    type names are wrapped via `SQL(...).join([])` to coerce to Composed.
    if col.logical_type is LogicalType.DECIMAL:
        precision = col.precision if col.precision is not None else 38
        scale = col.scale if col.scale is not None else 0
        type_sql = SQL("NUMERIC({}, {})").format(
            SQL(str(precision)), SQL(str(scale))
        )
    elif col.logical_type is LogicalType.STRING:
        if col.max_length is not None:
            type_sql = SQL("VARCHAR({})").format(SQL(str(col.max_length)))
        else:
            type_sql = SQL(" ").join([SQL("TEXT")])
    else:
        type_sql = SQL(" ").join([SQL(_LOGICAL_TO_PG[col.logical_type])])

    # 2. Assemble: "name type [NOT NULL] [PRIMARY KEY]"
    parts: list[Composed] = [type_sql]
    if not col.nullable:
        parts.append(SQL(" ").join([SQL("NOT NULL")]))
    if col.is_primary_key:
        parts.append(SQL(" ").join([SQL("PRIMARY KEY")]))
    return SQL(" ").join([Identifier(col.name), *parts])


class PostgresRepository:
    """PostgreSQL implementation of the data-access Protocols.

    Implements `SchemaProvider`, `DataProvider`, and `QueryProvider`
    (the latter via the class satisfying the empty Protocol). A single
    `psycopg.Connection` is held for the lifetime of the instance; use it
    as a context manager or call `close()` when done.
    """

    def __init__(
        self,
        conn: DictConnection | None = None,
        config: PostgresConfig | None = None,
    ) -> None:
        if conn is None:
            conn = connect(config)
        self._conn: DictConnection = conn

    # --- SchemaProvider ---

    def create_table(self, table_def: TableDef) -> None:
        """Materialize a table from an engine-neutral `TableDef` (DDL rendered here)."""
        column_clauses = [_render_column_ddl(col) for col in table_def.columns]
        ddl = SQL("CREATE TABLE IF NOT EXISTS {} (\n    {}\n)").format(
            Identifier(table_def.name),
            SQL(",\n    ").join(column_clauses),
        )
        with self._conn.cursor() as cur:
            cur.execute(ddl)
        self._conn.commit()

    def drop_table(self, name: str) -> None:
        """Drop a table by name (IF EXISTS to be idempotent)."""
        with self._conn.cursor() as cur:
            cur.execute(
                SQL("DROP TABLE IF EXISTS {} CASCADE").format(Identifier(name))
            )
        self._conn.commit()

    def table_exists(self, name: str) -> bool:
        """Check whether a table exists in the current schema (public)."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = %s"
                ")",
                (name,),
            )
            row = cur.fetchone()
        return bool(row is not None and bool(row.get("exists", False)))

    # --- DataProvider ---

    def load_rows(self, table_name: str, rows: list[Row]) -> LoadResult:
        """Bulk-load validated row models into `table_name`.

        Accepts `OrderRow` / `ReturnRow` / `PersonRow`. Uses batch INSERT
        (a future optimization may use `COPY`; the bulk-load semantic stays
        the same so the BigQuery adapter can map to a load job). Per FR-015,
        any constraint violation surfaces as a clear error — no silent
        partial load.

        Column-name resolution: the DB columns are created from the source
        Excel column names (title-case, e.g. "Row ID"), which are stored on
        each Pydantic field as `validation_alias`. We resolve each model
        field to its `validation_alias` (falling back to the field name) so
        the INSERT targets the correct DB columns.
        """
        if not rows:
            return LoadResult(table_name=table_name, rows_loaded=0)

        # All rows are the same type; introspect the first for field list.
        first = rows[0]
        model_cls = type(first)
        # Map snake_case model field -> DB column name (the validation_alias,
        # which holds the source Excel column name = DB column name).
        field_info_map: dict[str, str] = {}
        for fname, finfo in model_cls.model_fields.items():
            # validation_alias may be an AliasChoices; prefer its first choice.
            alias = finfo.validation_alias
            if alias is None:
                field_info_map[fname] = fname
            else:
                # AliasChoices stores choices; we take the single string alias.
                choices = getattr(alias, "choices", None)
                if choices:
                    field_info_map[fname] = str(choices[0])
                else:
                    field_info_map[fname] = str(alias)

        db_columns = list(field_info_map.values())
        col_sql = SQL(", ").join(Identifier(c) for c in db_columns)
        placeholders = SQL(", ").join(SQL("%s") for _ in db_columns)
        insert_sql = SQL("INSERT INTO {} ({}) VALUES ({})").format(
            Identifier(table_name), col_sql, placeholders
        )

        # Serialize each row via the model field attribute (snake_case name).
        attr_names = list(field_info_map.keys())
        tuples: list[tuple[object, ...]] = [
            tuple(cast(object, getattr(row, a)) for a in attr_names) for row in rows
        ]

        with self._conn.cursor() as cur:
            cur.executemany(insert_sql, tuples)
            rows_loaded = len(tuples)
        self._conn.commit()
        return LoadResult(table_name=table_name, rows_loaded=rows_loaded)

    def find_orders_by_region(self, region: str) -> list[OrderRow]:
        """Semantic query — return Orders rows for a region (future v2.0 RLS hook).

        At this baseline, does NOT enforce RLS (governance is v2.0 scope).
        Reads as dicts and validates each through `OrderRow.model_validate`
        so raw dicts never cross the boundary.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                SQL(
                    "SELECT * FROM {} WHERE \"Region\" = %s"
                ).format(Identifier("Orders")),
                (region,),
            )
            raw_rows = cur.fetchall()
        return [OrderRow.model_validate(r) for r in raw_rows]

    def count_rows(self, table_name: str) -> int:
        """Return the row count of a table (used by the validator)."""
        with self._conn.cursor() as cur:
            cur.execute(SQL("SELECT count(*) AS n FROM {}").format(Identifier(table_name)))
            row = cur.fetchone()
        if row is None:
            return 0
        return int(cast(int, row.get("n", 0)))

    def list_tables(self) -> list[str]:
        """List user tables in the `public` schema of the warehouse database."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "ORDER BY tablename"
            )
            rows = cur.fetchall()
        return [str(r["tablename"]) for r in rows if r is not None]

    # --- QueryProvider ---

    def execute_readonly_query(
        self, sql: str, table_def: TableDef
    ) -> list[QueryRow]:
        """Execute a validated read-only SELECT and return typed rows.

        The caller MUST validate the SQL via `SqlValidator` before calling
        this method. The adapter executes the query as-is (the SQL is already
        confirmed to be a single SELECT on the specified table) and maps each
        result row to a `QueryRow` model.

        Per FR-009/FR-010: raw dicts never cross the boundary — each row is
        converted to a `QueryRow` before returning.
        """
        with self._conn.cursor() as cur:
            cur.execute(sql)  # validated SELECT, safe to execute directly
            raw_rows = cur.fetchall()
        return [
            QueryRow(data={k: v for k, v in dict(r).items() if v is not None})
            for r in raw_rows
            if r is not None
        ]

    # --- Connection lifecycle ---

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "PostgresRepository":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


__all__ = ["PostgresRepository"]
