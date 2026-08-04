# Contract: Data-Access Layer

**Feature**: 001-data-genai-platform-baseline
**Date**: 2026-07-30
**Related**: [research.md](../research.md) · [data-model.md](../data-model.md)

> Defines the engine-neutral typed interfaces (Protocols) and contract models that ALL data access flows through. Engine-specific code (PostgreSQL now, BigQuery later) is confined to adapter implementations and MUST NOT be imported by upstream domains. See constitution Principle III.

## Interface Boundaries (Protocols)

The data-access layer exposes **three `typing.Protocol` interfaces** in `src/data_access/interfaces.py`. Adapters (e.g., `src/data_access/adapters/postgres/repository.py`) implement these Protocols; upstream code depends only on the Protocols + the contract models below.

### `SchemaProvider` — schema/materialization contract

Responsible for creating/dropping tables from the EDA-inferred schema.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `create_table(table_def)` | `TableDef` | `None` | Materialize a table from an engine-neutral `TableDef`. Engine renders DDL (PG: `CREATE TABLE ...` via `psycopg.sql`; BQ: native schema API) |
| `drop_table(name)` | `str` | `None` | Drop a table by name |
| `table_exists(name)` | `str` | `bool` | Check existence |

### `DataProvider` — read/write contract

Responsible for typed row I/O. All methods accept and return Pydantic models — never `dict` or DBAPI rows.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `load_rows(table_name, rows)` | `str`, `list[Row]` | `LoadResult` | Bulk-load validated rows. PG adapter uses `COPY`/batch insert; BQ adapter maps to a load job. **Bulk-load semantic** so BQ can use load jobs |
| `find_orders_by_region(region)` | `str` | `list[OrderRow]` | Semantic query — region filter (future v2.0 RLS hook). PG: `SELECT ... WHERE region = %s`; BQ: equivalent filter |
| `count_rows(table_name)` | `str` | `int` | Row count for a table (used by the validator) |
| `list_tables()` | — | `list[str]` | List tables in the warehouse schema |

### `QueryProvider` — typed query contract (future v1.x Text-to-SQL hook)

Reserved interface for semantic queries the upcoming Text-to-SQL layer will use. **No raw `execute_sql(sql: str)` method** — see Risks in research.md (would re-couple upstream to PG-flavored SQL and break BigQuery).

| Method | Input | Output | Semantics |
|---|---|---|---|
| *(reserved)* | — | — | Methods will be added per Text-to-SQL v1.0/1.1 spec — always semantic, always typed |

## Contract Models (Pydantic v2, in `src/contracts/data_access.py`)

These models are the shared currency that crosses the data-access boundary. Raw `dict`/DBAPI tuples NEVER cross it.

### Engine-neutral type enum

```python
class LogicalType(str, Enum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"   # carries precision + scale
    STRING = "STRING"     # carries max_length
    TIMESTAMP = "TIMESTAMP"
    BOOLEAN = "BOOLEAN"
```

### `ColumnDef`

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Column name |
| `logical_type` | `LogicalType` | Engine-neutral type |
| `precision` | `int \| None` | For DECIMAL |
| `scale` | `int \| None` | For DECIMAL |
| `max_length` | `int \| None` | For STRING → PG `VARCHAR(n)` |
| `nullable` | `bool` | True if NULLs allowed |
| `is_primary_key` | `bool` | PK flag |
| `allowed_values` | `list[str] \| None` | For enum-like STRING columns (documentation + future validation) |

### `TableDef`

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Table name |
| `columns` | `list[ColumnDef]` | Ordered column definitions |
| `description` | `str` | Business description (from Kaggle semantics) |
| `foreign_keys` | `list[ForeignKeyDef]` | FK-like relationships |

### `ForeignKeyDef`

| Field | Type | Notes |
|---|---|---|
| `column` | `str` | Local column |
| `references_table` | `str` | Target table |
| `references_column` | `str` | Target column |

### Row models (one per table)

`OrderRow`, `ReturnRow`, `PersonRow` — Pydantic v2 models with fields typed per `data-model.md`:
- Money fields: `Decimal` (NOT `float`).
- Date fields: `datetime`.
- Enum-like columns: `str` with `allowed_values` documented (validation optionally via `Literal`, but keep `str` for forward-compat).
- `OrderRow.postal_code`: `str | None` (80% null).
- `ReturnRow.returned`: `str` (always `"Yes"` but kept as `str` not `Literal["Yes"]` to detect source drift).
- `PersonRow.person`: `str` (normalized at load — non-breaking spaces stripped).

### `LoadResult`

| Field | Type | Notes |
|---|---|---|
| `table_name` | `str` | Table loaded |
| `rows_loaded` | `int` | Count of rows inserted |
| `rows_rejected` | `int` | Count of rows that failed validation (should be 0 or raise) |
| `errors` | `list[str]` | Per-row error details (for fail-fast reporting per FR-015) |

## Adapter Conformance

- `tests/contract/` MUST assert every public Protocol method is typed to accept/return contract models — never `Any`/`dict`.
- Use `@runtime_checkable` on the Protocols so `tests/contract/` can assert `isinstance(adapter, DataProvider)` conformance.
- The PostgreSQL adapter (`src/data_access/adapters/postgres/`) is the only implementation in this baseline; `src/data_access/adapters/bigquery/` is a stub placeholder for the future migration (no implementation).

## Out of Scope for This Contract

- Semantic Layer with RBAC/RLS — v2.0 scope (constitution Principle IV). The `find_orders_by_region` method exists as a hook but does NOT enforce RLS at this baseline.
- Text-to-SQL query interface — v1.0/1.1 scope (`QueryProvider` is reserved).
- Raw SQL escape hatches — explicitly NOT on the shared Protocol.
