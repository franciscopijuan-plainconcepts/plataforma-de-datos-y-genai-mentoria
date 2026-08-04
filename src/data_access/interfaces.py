"""Engine-neutral data-access Protocol interfaces.

Upstream code depends ONLY on these Protocols and the typed contract models
in `src/contracts/data_access.py`. Engine-specific implementations live in
`src/data_access/adapters/<engine>/` (constitution Principle III).

Design rules (see research.md Part B & contracts/data_access.md):
- `typing.Protocol` (structural typing) + `@runtime_checkable` so
  `tests/contract/` can assert adapter conformance.
- Every method is typed to accept/return contract models (Pydantic v2) —
  NEVER `Any`/`dict`/DBAPI rows.
- NO generic `execute_sql(sql: str)` escape hatch: it would re-couple upstream
  code to PG-flavored SQL and silently break on BigQuery. Raw-SQL escape
  hatches, if ever needed, live as engine-specific methods on the adapter
  class itself, never on these shared Protocols.
- `QueryProvider` is reserved for the future Text-to-SQL layer (v1.0/1.1); it
  is intentionally empty at this baseline.

Reference: specs/001-data-genai-platform-baseline/contracts/data_access.md
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.contracts.data_access import LoadResult, OrderRow, Row, TableDef


@runtime_checkable
class SchemaProvider(Protocol):
    """Schema/materialization contract — create/drop tables from inferred schema.

    Engine renders DDL internally: PG via `psycopg.sql`; BQ via its native
    schema API.
    """

    def create_table(self, table_def: TableDef) -> None:
        """Materialize a table from an engine-neutral `TableDef`."""
        ...

    def drop_table(self, name: str) -> None:
        """Drop a table by name."""
        ...

    def table_exists(self, name: str) -> bool:
        """Check whether a table exists in the warehouse schema."""
        ...


@runtime_checkable
class DataProvider(Protocol):
    """Read/write contract for typed row I/O.

    All methods accept and return contract models — never `dict` or DBAPI
    rows. `load_rows` is bulk-load semantic so the future BigQuery adapter
    can map it to a load job rather than forcing INSERT-per-row.
    """

    def load_rows(self, table_name: str, rows: list[Row]) -> LoadResult:
        """Bulk-load validated rows into `table_name`.

        Accepts any of the per-table row models (OrderRow / ReturnRow /
        PersonRow). Per FR-015, the first invalid row raises a ValidationError
        before producing a LoadResult (no silent partial load).
        """
        ...

    def find_orders_by_region(self, region: str) -> list[OrderRow]:
        """Semantic query — return Orders rows for a region.

        Future v2.0 RLS hook. At this baseline it does NOT enforce row-level
        security (governance is v2.0 scope); it returns typed OrderRow models
        only.
        """
        ...

    def count_rows(self, table_name: str) -> int:
        """Return the row count of a table (used by the validator)."""
        ...

    def list_tables(self) -> list[str]:
        """List tables in the warehouse schema."""
        ...


@runtime_checkable
class QueryProvider(Protocol):
    """Reserved typed-query contract for the future Text-to-SQL layer (v1.0/1.1).

    Intentionally empty at this baseline. When added, methods will be semantic
    and always typed — never a raw `execute_sql(sql: str)` escape hatch.
    """

    # No methods at this baseline. Methods will be added per the Text-to-SQL
    # v1.0/1.1 feature spec.
