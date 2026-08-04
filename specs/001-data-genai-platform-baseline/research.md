# Research: Data and GenAI Platform – Baseline

**Phase**: 0 (Outline & Research)
**Feature**: 001-data-genai-platform-baseline
**Date**: 2026-07-30
**Status**: Complete — all NEEDS CLARIFICATION items resolved

> This document consolidates two research threads executed for the baseline plan:
> 1. **EDA** — direct inspection of `Global Superstore Data.xlsx` (pandas + openpyxl) to derive the schema from real content rather than hardcoding types.
> 2. **Data-access layer & tooling best practices** — opinionated recommendations grounding the architecture in the constitution (engine-agnostic data-access, typed contracts, reproducible MLOps).

---

## Part A — EDA Findings (from `Global Superstore Data.xlsx`)

### A.1 Sheet Inventory

The workbook contains exactly three sheets, matching the three logical tables:

| Sheet | Rows | Cols | Maps to logical table |
|---|---|---|---|
| `Orders` | 51,290 | 24 | Orders (Transactional Logs) |
| `Returns` | 2,033 | 3 | Returns (Reverse Logistics) |
| `People` | 24 | 2 | People (Sales Governance) |

Kaggle semantic alignment: **confirmed**. The three sheet names map cleanly to the Kaggle descriptions referenced in the spec — Orders (transactional logs of order lines), Returns (records of returned orders), People (regional sales-person governance mapping).

### A.2 Per-Sheet EDA

#### Sheet: `Orders` (51,290 rows × 24 cols)

| Column | pandas dtype | non-null | null | unique | Notes |
|---|---|---|---|---|---|
| `Row ID` | int64 | 51,290 | 0 | 51,290 | **Perfect PK**: unique, integer, non-null |
| `Order ID` | str | 51,290 | 0 | 25,728 | One order has multiple line-items → not unique (1:N) |
| `Order Date` | datetime64[us] | 51,290 | 0 | 1,429 | Range: 2014-01-01 → 2017-12-31 |
| `Ship Date` | datetime64[us] | 51,290 | 0 | 1,463 | Range: 2014-01-03 → 2018-01-07 |
| `Ship Mode` | str | 51,290 | 0 | 4 | Categorical: Standard/Second/First Class/Same Day |
| `Customer ID` | str | 51,290 | 0 | 17,415 | — |
| `Customer Name` | str | 51,290 | 0 | 796 | 796 names for 17,415 IDs (1:N) |
| `Segment` | str | 51,290 | 0 | 3 | Categorical: Home Office/Consumer/Corporate |
| `Postal Code` | float64 | 9,994 | **41,296** | 631 | **80% NULL** — only US/Canada rows populated |
| `City` | str | 51,290 | 0 | 3,650 | — |
| `State` | str | 51,290 | 0 | 1,106 | — |
| `Country` | str | 51,290 | 0 | 165 | — |
| `Region` | str | 51,290 | 0 | 23 | Categorical; shared with Returns & People |
| `Market` | str | 51,290 | 0 | 5 | Categorical: Asia Pacific/Europe/Africa/LATAM/USCA |
| `Product ID` | str | 51,290 | 0 | 3,788 | 1:N with Product Name |
| `Product Name` | str | 51,290 | 0 | 3,788 | — |
| `Sub-Category` | str | 51,290 | 0 | 17 | Categorical |
| `Category` | str | 51,290 | 0 | 3 | Categorical: Furniture/Office Supplies/Technology |
| `Sales` | float64 | 51,290 | 0 | 22,995 | Money → **NUMERIC(p,s)**. Range 0.444 → 22,638.48 |
| `Quantity` | int64 | 51,290 | 0 | 14 | Range 1 → 14 |
| `Discount` | float64 | 51,290 | 0 | 27 | Range 0.0 → 0.85; odd values like 0.402, 0.002 present |
| `Profit` | float64 | 51,290 | 0 | 24,575 | Money → **NUMERIC(p,s)**. Range -6,599.978 → 8,399.976 (can be negative) |
| `Shipping Cost` | float64 | 51,290 | 0 | 16,452 | Money → **NUMERIC(p,s)**. Range 1.002 → 933.57 |
| `Order Priority` | str | 51,290 | 0 | 4 | Categorical: Medium/High/Low/Critical |

#### Sheet: `Returns` (2,033 rows × 3 cols)

| Column | pandas dtype | non-null | null | unique | Notes |
|---|---|---|---|---|---|
| `Returned` | str | 2,033 | 0 | 1 | Always `"Yes"` (single value) |
| `Order ID` | str | 2,033 | 0 | 1,970 | **63 duplicate Order IDs** → likely multi-line returns; PK must be composite |
| `Region` | str | 2,033 | 0 | 23 | Shared with Orders & People |

#### Sheet: `People` (24 rows × 2 cols)

| Column | pandas dtype | non-null | null | unique | Notes |
|---|---|---|---|---|---|
| `Person` | str | 24 | 0 | 24 | **Unique → PK candidate**. Contains non-breaking spaces (`\xa0`) in some names |
| `Region` | str | 24 | 0 | 24 | 24 regions (People splits Canada into Eastern/Western; Orders lists Canada as one). 22 of 24 overlap with Orders |

### A.3 Shared Columns & Relationships (value-overlap confirmed)

| Column | Present in | Overlap evidence |
|---|---|---|
| `Order ID` | Orders, Returns | 1,970 of Returns' values exist in Orders (Returns.Order ID → Orders.Order ID) |
| `Region` | Orders, Returns, People | Orders: 23 | Returns: 23 | People: 24. Orders↔Returns: 23 shared. Orders↔People: 22 shared. Returns↔People: 22 shared. |

**Implied relationships**:
- `Returns.Order ID` → `Orders.Order ID` (a return refers to an order; 1:1 at the order level, but Returns carries duplicate Order IDs for multi-line orders).
- `People.Region` → `Orders.Region` (regional sales-person governs rows in Orders — basis for future v2.0 RLS).
- `People.Region` → `Returns.Region` (same regional governance applies to returns).

### A.4 Data-Quality Observations (affecting the loader)

1. **`Postal Code` is 80% NULL** in Orders (only US/Canada rows have postal codes). The column MUST be nullable; loader MUST NOT fail on these nulls.
2. **`Returns.Order ID` has 63 duplicates** (2,033 rows, 1,970 unique). A simple `PRIMARY KEY (Order ID)` will FAIL. PK must be composite (e.g., include a row-ordinal/surrogate) or Returns must use a surrogate `Return ID`.
3. **`Returns.Returned` is a degenerate column** (always `"Yes"`). The presence of the row itself encodes "returned"; the column adds no information but MUST still be loaded for fidelity.
4. **Non-breaking spaces (`\xa0`)** appear in some `People.Person` names (e.g., `"Andile\xa0Ihejirika"`). Loader/dictionary MUST normalize or document this.
5. **`Discount` has irregular values** like `0.402`, `0.002`, `0.602`, `0.202` — not clean percentages. These are real data (not NULLs); the dictionary MUST document that Discount is a fractional amount (0.0–0.85) with some non-round values.
6. **`Profit` is signed** (negative losses possible). Schema MUST allow negatives.
7. **Region taxonomy mismatch**: People has `Eastern Canada`/`Western Canada` (24 regions); Orders/Returns have a single `Canada` (23 regions). 22 of 24 People regions overlap with Orders. The data model MUST keep `Region` as a free-text `VARCHAR` (not an enum) until v2.0 governance consolidates it.

---

## Part B — Technology & Architecture Decisions

### Decision: PostgreSQL Driver

**Decision**: Use **`psycopg` (v3), synchronous API only**, confined to `src/data_access/adapters/postgres/`.

**Rationale**: This baseline is a local CLI / ingestion / validation tool, not a web server — single user, single local DB, sequential command-driven workflows. Async (`asyncpg`, psycopg-async) would propagate `async`/`await` through the whole stack with zero throughput benefit. `psycopg` v3 is the actively-developed successor to `psycopg2`: first-class static-typing support, server-side parameter binding, a Python-native `COPY` API for fast bulk load, and pluggable `row_factory` hooks that slot cleanly behind a typed Repository. `psycopg2` is legacy/maintenance-only. The driver NEVER appears upstream — only inside the postgres adapter.

**Alternatives considered**:
- `psycopg2` (legacy, no modern typing) — rejected.
- `asyncpg` (async-only, would force async contagation) — rejected for a sync CLI context.
- `pg8000` (pure-Python, slower) — rejected.
- Direct `SQLAlchemy` used as the engine-agnostic interface — see next decision (rejected as the *abstraction* layer).

### Decision: Data-Access Layer Pattern

**Decision**: **Hand-rolled `typing.Protocol`-based interfaces with per-engine adapters** (NOT SQLAlchemy-as-abstraction, NOT an ABC).

**Rationale**:
- `typing.Protocol` is structural: adapters don't inherit a shared base, test doubles are duck-typed, and it composes cleanly with Pyright strict + Pydantic v2. Use `@runtime_checkable` selectively so `tests/contract/` can assert conformance.
- An ABC would add inheritance ceremony and a shared base whose methods would leak engine assumptions.
- **SQLAlchemy Core is engine-agnostic for *relational* engines, but BigQuery is not relational** — no FKs, no sequences, no transactions, columnar/append execution, and load-jobs-not-`INSERT` for bulk. Forcing both engines through SQLAlchemy Core trades real engine leverage (psycopg `COPY` vs BQ load jobs) for false uniformity and tends to constrain to the lowest common denominator or leak PG assumptions upstream.
- The shared currency between adapters is the **Pydantic v2 contract models in `src/contracts/data_access.py`** (`Dataset`, `Table`, `Column`, `Row`) plus an engine-neutral **`LogicalType` enum** on `ColumnDef` (`STRING`, `INTEGER`, `DECIMAL(p,s)`, `TIMESTAMP`, `BOOLEAN`). Each adapter maps `LogicalType → engine DDL` internally (`VARCHAR(n)`/`NUMERIC(p,s)` for PG; `STRING`/`INT64`/`NUMERIC` for BQ). The PG adapter renders DDL via `psycopg.sql`; the future BQ adapter uses `google-cloud-bigquery`'s schema API.

**Endpoint flow**: Adapter methods accept and return Pydantic models (e.g., `list[OrderRow]`); raw `dict`/DBAPI tuples never cross the boundary. On read, PG adapter sets `conn.row_factory = dict_row` and pipes each dict through `OrderRow.model_validate(...)`. `tests/contract/` asserts every public Protocol method is typed to return/accept contract models — never `Any`/`dict`.

**Alternatives considered**:
- SQLAlchemy Core as the universal engine interface — rejected (BigQuery non-relational mismatch described above).
- ABC with shared base class — rejected (inheritance ceremony, leaks engine assumptions).

### Decision: Typed Ingestion Path

**Decision**: `pandas.read_excel → astype(inferred nullable dtypes) → TypeAdapter(list[ModelRow]).validate_python(df.to_dict("records")) → return list[ModelRow]`. The DataFrame NEVER escapes the ingestion module.

**Rationale**:
- Read with `pandas.read_excel(..., engine="openpyxl", sheet_name=...)`, then coerce dtypes *before* validation using the EDA-inferred schema, preferring **pandas nullable dtypes** (`"Int64"`, `"Float64"`, `"string"`, `"boolean"`) so missing values become `pandas.NA` → `None` on `to_dict`, which Pydantic `Optional[...]` accepts cleanly. Naive `float64` produces `NaN` for missing values that Pydantic `int` fields reject — avoid this trap explicitly (relevant to `Postal Code`).
- Validate at the boundary: `TypeAdapter(list[OrderRow]).validate_python(df.to_dict(orient="records"))` raises `ValidationError` with the offending row path → satisfies FR-013/FR-015 (fail fast, report offending rows, no silent partial load).
- **Type mapping (decide now)**:
  - `int64` → `int` (e.g., `Row ID`, `Quantity`)
  - `float64` money columns → **`Decimal`** (not `float`) — `Sales`, `Profit`, `Shipping Cost`, `Discount`. Decide now or pay float-drift debt later.
  - `datetime64[us]` → `datetime` (`Order Date`, `Ship Date`)
  - low-cardinality `object` → `Literal[...]` or `enum.Enum` (`Ship Mode`, `Segment`, `Market`, `Category`, `Sub-Category`, `Order Priority`)
  - free-text `object` → `str` (`City`, `State`, `Country`, `Region`, `Customer Name`, `Product Name`, IDs)
- DataFrame never leaks past the ingestion module; upstream strict-typed domains never import `pandas`.

**Alternatives considered**:
- Loading the raw DataFrame straight into PostgreSQL (skipping Pydantic validation) — rejected (violates typed-boundary constitution rule; no fail-fast on bad rows).
- Using `float` for money columns — rejected (float drift; `Decimal` + PG `NUMERIC(p,s)` is the correct choice).

### Decision: Minimal MLOps Footprint

**Decision**: Adopt a **minimal, honest** footprint now; defer MLflow/DVC until the first model is trained (v1.x+).

**Adopt NOW**:
- Reproducible environment via `uv` lockfile (deterministic, cross-platform).
- Deterministic Dockerized PostgreSQL + EDA-derived schema (regeneratable from source `.xlsx`).
- **A load artifact manifest** — a JSON/YAML file emitted by the loader capturing per-load provenance: source-file `sha256`, inferred schema version, per-table row counts, tool/library versions, git commit, timestamp. The validator consumes it (FR-014).
- Git tag `v0.x` per baseline release.

**Defer until v1.x+ (first model)**: MLflow (tracking server + model registry), DVC for training-data versioning, full experiment-tracking infra. Adopting these now with zero models is pure overhead; requirements will only be knowable once Text-to-SQL/model work begins.

**Rationale**: The artifact manifest + lockfile + git tags genuinely satisfy the *spirit* of "Reproducible MLOps" (versioned artifacts + reproducibility) for a no-models baseline without over-engineering.

**Alternatives considered**:
- Adopt MLflow from day one — rejected (no models yet; pure overhead; requirements unknowable pre-Text-to-SQL).
- Skip provenance entirely — rejected (would violate the spirit of Principle V and break FR-014 validation).

### Decision: Project Tooling

**Decision**: Use **`uv`** (over poetry).

**Rationale**: In 2026, `uv` (Astral, Rust-based, stable & production-ready) is the de-facto choice for a local CLI / data-engineering baseline: a universal lockfile, 10–100× faster resolution, and crucially **it installs and pins Python itself** — no separate `pyenv` needed, directly serving Story 3 / FR-013 ("reproduce the env from a clean clone with one bootstrap command"). It replaces `pip`, `pip-tools`, `pipx`, `poetry`, `pyenv`, and `virtualenv` — minimizing moving parts. Poetry is mature but slower and does not manage Python versions. Config in `pyproject.toml` under the standard PEP 621 `[project]` table; commit `uv.lock`; pin `requires-python = ">=3.11"`; run everything via `uv run pytest`, `uv run python -m src.cli`, `uv sync`.

**Alternatives considered**:
- `poetry` — slower, doesn't manage Python versions, surpassed by uv as community default in 2026.

---

## Part C — Risks & Caveats

- **psycopg 3 server-side-binding quirk**: Cannot parametrize DDL/`SET`/`NOTIFY` with the default extended-protocol cursor. Use `psycopg.sql.SQL`/`sql.Identifier` for identifier composition; for DDL with literals use `sql.SQL("... DEFAULT {}").format(...)` or a `ClientCursor`. Adapter must handle this.
- **pandas `object`/`NaN` trap**: Mixed-type or missing data shows up as `object`/`NaN`. Coerce to nullable dtypes pre-validation, or `int` Pydantic fields will reject `NaN`. This is the #1 reason ingestion→Pydantic validation "mysteriously" fails — directly relevant to the 80%-null `Postal Code`.
- **Decimal vs float for money**: Decided now — `Decimal` → PG `NUMERIC(p,s)` for `Sales`/`Profit`/`Shipping Cost`/`Discount`. Retrofitting later is painful.
- **Do not expose raw SQL on the shared Protocol**: A generic `execute_sql(sql: str)` on `DataProvider`/`QueryProvider` would re-couple upstream code to PG-flavored SQL and silently break on BigQuery. Protocol methods stay *semantic* (`find_orders_by_region`, `load_rows`, `create_table_from(TableDef)`); raw-SQL escape hatches, if any, live as engine-specific methods on the *adapter class itself*, never on the shared interface.
- **BigQuery ≠ PostgreSQL semantically**: Even with a clean Protocol, BQ has no FKs, no sequences, no transactions, and uses load jobs for bulk insert. Design `load_rows` to be "bulk-load semantic" so the BQ adapter can map to a load job rather than forcing `INSERT`-per-row.
- **Pyright strict + Pydantic v2 + pandas**: Pyright strict will complain about pandas' partially-typed API. Keep `pandas` usage confined to the ingestion module (a typed boundary that emits validated Pydantic models on exit), so upstream strict-typed domains never import `pandas`.
- **Returns PK**: 63 duplicate `Order ID` values in Returns → cannot use `Order ID` alone as PK. Use a surrogate `Return ID` (row-ordinal serial) or a composite key. Decision: surrogate `return_id SERIAL PRIMARY KEY`, keep `Order ID` as a non-unique indexed column (foreign-like link to Orders).
- **People name normalization**: Non-breaking spaces (`\xa0`) in `People.Person` MUST be normalized to regular spaces at load time (or documented) so RLS-by-name in v2.0 doesn't silently miss matches.
- **Region taxonomy mismatch**: People has `Eastern Canada`/`Western Canada` (24 regions) vs Orders/Returns `Canada` (23 regions). Keep `Region` as `VARCHAR` (not enum) until v2.0 governance consolidates the taxonomy.

---

## Resolution Summary (NEEDS CLARIFICATION items closed)

| Open clarification (from plan.md Technical Context) | Resolution |
|---|---|
| Exact PostgreSQL driver (sync vs async) | **psycopg v3, sync** — confined to postgres adapter |
| Final PostgreSQL data types per column | **Derived from EDA** — see `data-model.md` (Phase 1) for the full typed schema (money as `NUMERIC(p,s)`, IDs/Quantity as `INTEGER`, dates as `TIMESTAMP`, categoricals as `VARCHAR` with documented allowed values) |
| Minimal MLOps/versioning footprint | **uv lockfile + load artifact manifest + git tags**; defer MLflow/DVC until first model (v1.x+) |

All NEEDS CLARIFICATION items are resolved. Phase 1 design (data-model.md, contracts/, quickstart.md) can proceed.
