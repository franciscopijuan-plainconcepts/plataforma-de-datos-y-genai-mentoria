# Contract: Ingestion Pipeline (EDA → Schema → Load)

**Feature**: 001-data-genai-platform-baseline
**Date**: 2026-07-30
**Related**: [research.md](../research.md) · [data-model.md](../data-model.md) · [data_access.md](./data_access.md)

> Defines the typed contracts that flow through the ingestion pipeline: pandas reads the Excel → EDA infers a schema → Pydantic validates rows → the data-access layer materializes and loads them. The DataFrame NEVER escapes the ingestion module; only validated Pydantic models cross boundaries. See constitution Principles I, II, III.

## Pipeline Stages & Contracts

```
Global Superstore Data.xlsx
        │  (pandas.read_excel, confined to src/data_engineering/ingestion/)
        ▼
   DataFrame  ──►  EDA inference (src/data_engineering/eda/)
        │              produces SchemaInferenceResult
        ▼
   SchemaInferenceResult  ──►  TableDef / ColumnDef  (src/contracts/data_access.py)
        │
        ▼
   astype(nullable dtypes) → TypeAdapter(list[OrderRow|ReturnRow|PersonRow]).validate_python(...)
        │              produces list[Row]
        ▼
   DataProvider.load_rows(table_name, rows)   (via Protocol, in data-access layer)
        │              produces LoadResult
        ▼
   PostgreSQL (via PG adapter) + LoadArtifactManifest
```

## Contract Models (Pydantic v2, in `src/contracts/ingestion.py`)

### `ColumnProfile` (EDA output per column)

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Column name |
| `pandas_dtype` | `str` | Observed dtype |
| `non_null_count` | `int` | Non-null count |
| `null_count` | `int` | Null count |
| `unique_count` | `int` | Unique value count |
| `sample_values` | `list[str]` | First 5 non-null values (for documentation/debug) |
| `min_value` | `str \| None` | For numeric/date columns |
| `max_value` | `str \| None` | For numeric/date columns |
| `is_primary_key_candidate` | `bool` | True if unique + non-null + integer-like |
| `inferred_logical_type` | `LogicalType` | Best-guess engine-neutral type |

### `TableProfile` (EDA output per table/sheet)

| Field | Type | Notes |
|---|---|---|
| `sheet_name` | `str` | Source Excel sheet |
| `row_count` | `int` | Rows read |
| `column_count` | `int` | Columns read |
| `columns` | `list[ColumnProfile]` | Per-column profiles |
| `primary_key_candidate` | `list[str]` | Column(s) identified as PK |
| `duplicate_count_by_pk` | `dict[str, int]` | Duplicate counts (e.g., Returns.Order ID = 63) — flags need for surrogate PK |

### `SchemaInferenceResult` (top-level EDA output)

| Field | Type | Notes |
|---|---|---|
| `source_file` | `str` | Path to the .xlsx |
| `source_sha256` | `str` | Hash for provenance (manifest) |
| `tables` | `list[TableProfile]` | One per sheet |
| `shared_columns` | `list[SharedColumn]` | Columns appearing in >1 sheet |
| `inferred_at` | `datetime` | When the EDA ran |

### `SharedColumn`

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Shared column name (e.g., `Order ID`, `Region`) |
| `present_in` | `list[str]` | Sheets where it appears |
| `pairwise_overlap` | `dict[str, int]` | `"Orders<->Returns": 1970`, etc. |

### `LoadArtifactManifest` (provenance — see research.md "Minimal MLOps Footprint")

| Field | Type | Notes |
|---|---|---|
| `source_file` | `str` | Path to the .xlsx |
| `source_sha256` | `str` | Source hash |
| `schema_version` | `str` | EDA-inferred schema version (e.g., `"v1"`) |
| `loaded_at` | `datetime` | Load timestamp |
| `git_commit` | `str` | Git commit SHA |
| `tool_versions` | `dict[str, str]` | pandas, psycopg, pydantic, etc. |
| `per_table` | `list[TableLoadSummary]` | One per table |

### `TableLoadSummary`

| Field | Type | Notes |
|---|---|---|
| `table_name` | `str` | Table loaded |
| `row_count` | `int` | Rows loaded (must match EDA row_count) |
| `column_count` | `int` | Columns materialized |
| `load_result` | `LoadResult` | From the data-access layer |

## Validation Rules (enforced in the pipeline)

- **FR-013 / fail-fast**: If the Excel file is missing/corrupt, or a sheet has an unexpected name, EDA MUST raise a typed error before any DB writes occur.
- **FR-015**: Pydantic `TypeAdapter(list[Row]).validate_python(...)` validates every row; the FIRST invalid row raises `ValidationError` with the offending row path → no silent partial load.
- **Postal Code nulls**: `OrderRow.postal_code: str | None` accepts the 80% nulls (`pandas.NA` → `None` via nullable dtype coercion).
- **Money as `Decimal`**: `Sales`, `Profit`, `Shipping Cost`, `Discount` MUST be `Decimal` (coerced from float at the `astype` step, never allowed to enter Pydantic as `float`).
- **Person normalization**: `People.Person` values are normalized (`\xa0` → space, strip) BEFORE validation, so the PK stays clean.
- **Returns surrogate PK**: The loader assigns `Return ID` (row-ordinal 1..2033) at load time; `Order ID` is kept as a non-unique indexed column (not a PK).
- **Row-count reconciliation**: `LoadResult.rows_loaded` MUST equal `TableProfile.row_count` from the EDA — if not, the load FAILS (FR-015).
- **Provenance**: `LoadArtifactManifest` is written next to the load and consumed by the validator (FR-014).

## Boundary Enforcement

- `src/data_engineering/eda/` and `src/data_engineering/ingestion/` are the ONLY modules that may import `pandas` / `openpyxl`.
- `src/data_engineering/ingestion/` is the ONLY module that converts DataFrame → Pydantic models; it returns `list[Row]`, never a DataFrame.
- `src/data_engineering/` depends on `src/contracts/` (typed models) and the data-access Protocols — it does NOT import `psycopg` or any engine-specific library.
- `tests/contract/` asserts: (a) no `pandas` import outside `data_engineering/eda|ingestion`, (b) no `psycopg` import outside `data_access/adapters/postgres/`, (c) all cross-boundary payloads are Pydantic models.
