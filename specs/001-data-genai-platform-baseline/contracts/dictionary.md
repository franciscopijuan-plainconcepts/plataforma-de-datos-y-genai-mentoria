# Contract: Data Dictionary Generation

**Feature**: 001-data-genai-platform-baseline
**Date**: 2026-07-30
**Related**: [research.md](../research.md) · [data-model.md](../data-model.md)

> Defines the contracts for generating the comprehensive data dictionary that integrates Kaggle semantic descriptions (Orders: Transactional Logs; Returns: Reverse Logistics; People: Sales Governance) with the EDA-discovered types. See spec FR-008 through FR-012.

## Generation Flow

```
SchemaInferenceResult (from EDA)  ──┐
                                    ├──►  DictionaryGenerator  ──►  DataDictionaryDocument
KaggleSemanticSource (curated)    ──┘                                     │
                                                                         ▼
                                                            data_dictionary.md (committed artifact)
```

- **Inputs** (typed contracts):
  - `SchemaInferenceResult` (the EDA-derived schema with observed types/stats — see [ingestion.md](./ingestion.md)).
  - `KaggleSemanticSource` (curated table/column semantic descriptions, hardcoded in `src/data_engineering/dictionary/semantic_source.py`).
- **Output**: `DataDictionaryDocument` (Pydantic model) rendered to a committed Markdown file.

## Contract Models (Pydantic v2, in `src/contracts/dictionary.py`)

### `KaggleSemanticSource` (curated, in code)

Top-level: three table-level semantic blocks + per-column descriptions.

| Field | Type | Notes |
|---|---|---|
| `table_semantics` | `dict[str, TableSemantic]` | Keyed by table name (Orders/Returns/People) |

### `TableSemantic`

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Table name |
| `kaggle_label` | `str` | e.g., "Transactional Logs", "Reverse Logistics", "Sales Governance" |
| `purpose` | `str` | Business description (1–3 sentences) |

### `ColumnSemantic`

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Column name |
| `business_description` | `str` | What the column means in business terms |
| `is_key` | `bool` | True if PK or FK-like |
| `key_kind` | `Literal["primary", "foreign", None]` | "primary", "foreign", or None |

### `DictionaryEntry` (per column — merged EDA + semantic)

| Field | Type | Source |
|---|---|---|
| `name` | `str` | EDA |
| `business_description` | `str` | Kaggle semantic source |
| `logical_type` | `LogicalType` | EDA-inferred |
| `postgres_type` | `str` | PG adapter mapping (e.g., `NUMERIC(12,4)`) |
| `nullable` | `bool` | EDA |
| `is_key` | `bool` | Semantic source |
| `key_kind` | `Literal["primary", "foreign", None]` | Semantic source |
| `allowed_values` | `list[str] \| None` | EDA (for enum-like columns with ≤30 uniques) |
| `min_value` | `str \| None` | EDA (numeric/date) |
| `max_value` | `str \| None` | EDA (numeric/date) |
| `unique_count` | `int` | EDA |
| `data_quality_notes` | `list[str]` | EDA-derived caveats (e.g., "80% NULL", "contains non-breaking spaces", "signed values allowed") |

### `TableDictionary`

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Table name |
| `kaggle_label` | `str` | e.g., "Transactional Logs" |
| `purpose` | `str` | Business purpose |
| `primary_key` | `list[str]` | PK column(s) |
| `relationships` | `list[RelationshipEntry]` | FK-like links to other tables |
| `columns` | `list[DictionaryEntry]` | One per column |

### `RelationshipEntry`

| Field | Type | Notes |
|---|---|---|
| `from_column` | `str` | Local column |
| `to_table` | `str` | Target table |
| `to_column` | `str` | Target column |
| `cardinality` | `Literal["1:N", "N:1", "1:1"]` | Observed cardinality |

### `DataDictionaryDocument` (top-level)

| Field | Type | Notes |
|---|---|---|
| `generated_at` | `datetime` | When the dictionary was generated |
| `source_file` | `str` | Source .xlsx path |
| `source_sha256` | `str` | Source hash (links to load manifest) |
| `tables` | `list[TableDictionary]` | One per table (Orders, Returns, People) |

## Acceptance Criteria (mapped to spec FRs)

- **FR-008**: `DataDictionaryDocument.tables` MUST contain exactly three entries (Orders, Returns, People) covering all columns.
- **FR-009**: Every `DictionaryEntry` MUST have `name`, `business_description`, `logical_type`/`postgres_type`, `nullable`, and `is_key`/`key_kind`.
- **FR-010**: Every `TableDictionary` MUST have `name`, `purpose` (Kaggle label), `primary_key`, and `relationships`.
- **FR-011**: `RelationshipEntry` MUST document cross-table links (`Returns.Order ID → Orders.Order ID`, `People.Region → Orders.Region`, `People.Region → Returns.Region`).
- **FR-012**: The generator MUST be regeneratable from the loaded schema (it consumes `SchemaInferenceResult`), so it stays in sync with the warehouse.

## Data-Quality Notes (pre-curated, from EDA — see research.md Part A.4)

The generator MUST attach these notes to the relevant `DictionaryEntry.data_quality_notes`:

- `Orders.Postal Code`: "80% NULL — only US/Canada rows have postal codes; nullable."
- `Orders.Discount`: "Fractional amount 0.0–0.85; contains non-round values like 0.402, 0.002 (kept as-is)."
- `Orders.Profit`: "Signed — negative values allowed (losses)."
- `Returns.Returned`: "Degenerate column — always 'Yes'; the row's presence encodes 'returned'."
- `Returns.Order ID`: "63 duplicate values across 2,033 rows — multi-line returns; `Order ID` is NOT the PK (surrogate `Return ID` is)."
- `People.Person`: "Normalized at load: non-breaking spaces (`\xa0`) replaced with regular spaces."
- `People.Region` / `Orders.Region`: "Region taxonomy mismatch — People splits Canada into Eastern/Western Canada (24 regions) vs Orders' single Canada (23 regions); 22 of 24 overlap. Kept as VARCHAR, not enum — resolution is v2.0 Semantic-Layer scope."

## Output Format

The committed artifact (`data_dictionary.md`) is rendered from `DataDictionaryDocument`:
- One section per table with the Kaggle label as subtitle.
- A column table per table with: name, business description, type, nullable, key, allowed values, DQ notes.
- A relationships block linking the three tables.

The Markdown is regenerated by the CLI `generate-dictionary` command and committed alongside the plan (regeneratable on a fresh machine per FR-012).
