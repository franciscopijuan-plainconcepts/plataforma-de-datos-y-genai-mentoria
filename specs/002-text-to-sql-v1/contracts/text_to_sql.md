# Contract: Text-to-SQL Pipeline

**Feature**: 002-text-to-sql-v1
**Date**: 2026-08-11
**Related**: [research.md](../research.md) · [data-model.md](../data-model.md)

> Defines the typed interfaces and contract models that flow through the Text-to-SQL pipeline: NL question → prompt → LLM → SQL → validate → execute → typed result. Cross-domain traffic (AI Engineering → Data Access) flows only through the `QueryProvider` Protocol. See constitution Principles I, II, III.

## Pipeline Stages & Contracts

```
NLQuestion (user input)
        │
        ▼
PromptBuilder.build_prompt(question, dictionary)  ──►  str (prompt)
        │
        ▼
LlmClient.generate_sql(prompt)  ──►  GeneratedSql
        │
        ▼
SqlValidator.validate(generated_sql, orders_schema)  ──►  ValidationResult
        │
        ├─ [rejected] → TextToSqlResponse(validation=rejected, query_result=None)
        │
        ▼ [accepted]
QueryProvider.execute_readonly_query(sql, table_def)  ──►  list[QueryRow]
        │
        ▼
QueryResult(sql, rows, row_count, latency_ms, error)
        │
        ▼
TextToSqlResponse(question, generated_sql, validation, query_result, error)
```

## Interface Boundaries

### `LlmClient` — LLM integration (in `src/ai_engineering/llm_client.py`)

Typed wrapper around the OpenAI Python SDK. The ONLY module that imports `openai`.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `__init__(config)` | `LlmConfig` | — | Creates the OpenAI client (api_key, base_url, httpx verify=False for Forge) |
| `generate_sql(prompt)` | `str` | `GeneratedSql` | Sends the prompt to the LLM and returns the generated SQL + metadata |

**Boundary rule**: `openai` and `httpx` imports are confined to this module. The boundary contract test asserts this.

### `PromptBuilder` — prompt construction (in `src/ai_engineering/prompt_builder.py`)

Builds the LLM prompt from the `DataDictionaryDocument` + the NL question. The ONLY module that serializes `DataDictionaryDocument` into prompt format.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `build_prompt(question, dictionary)` | `NLQuestion`, `DataDictionaryDocument` | `str` | Returns the full prompt: system instruction + condensed schema + question (see research.md Part B) |

**Boundary rule**: Does NOT call the LLM. Does NOT import `openai`. Pure transformation.

### `SqlValidator` — SQL validation (in `src/ai_engineering/sql_validator.py`)

Validates the LLM-generated SQL before execution. Pure function, no LLM, no DB.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `validate(sql, table_def)` | `str`, `TableDef` | `ValidationResult` | Checks: SELECT-only, single-statement, no comments, no forbidden keywords, Orders-table-only, existing-columns-only (see research.md Part A) |

**Boundary rule**: Does NOT call the LLM. Does NOT execute SQL against the DB. Pure validation logic. Fully unit-testable.

### `TextToSqlPipeline` — orchestration (in `src/ai_engineering/pipeline.py`)

Orchestrates the full pipeline: prompt → LLM → validate → execute → return.

| Method | Input | Output | Semantics |
|---|---|---|---|
| `run(question)` | `NLQuestion` | `TextToSqlResponse` | Full pipeline. Builds prompt → calls LLM → validates SQL → executes (if accepted) → returns typed response. Catches and surfaces all errors (FR-013). |

**Dependencies** (injected, typed):
- `PromptBuilder` (or a callable that builds the prompt).
- `LlmClient` (or a callable that generates SQL).
- `SqlValidator` (or a callable that validates).
- `QueryProvider` (the existing data-access Protocol — for execution).

**Boundary rule**: The pipeline imports only contracts (`src/contracts/text_to_sql.py`, `src/contracts/dictionary.py`, `src/contracts/data_access.py`) and the `QueryProvider` Protocol. It does NOT import `openai`, `psycopg`, or `pandas`.

### `QueryProvider` — Protocol extension (in `src/data_access/interfaces.py`)

The reserved `QueryProvider` Protocol (empty in the baseline) gains one method:

| Method | Input | Output | Semantics |
|---|---|---|---|
| `execute_readonly_query(sql, table_def)` | `str`, `TableDef` | `list[QueryRow]` | Executes a pre-validated read-only SELECT and returns typed rows. NOT a generic `execute_sql` — the caller MUST validate first. The adapter maps DB rows → `QueryRow` models. |

**Design rule** (from research.md Part C): This is NOT a raw `execute_sql(sql: str)` escape hatch. It is a purpose-built read-only method that accepts validated SQL and returns typed models. The method name signals the read-only contract.

## Contract Models (Pydantic v2, in `src/contracts/text_to_sql.py`)

See [data-model.md](../data-model.md) for the full field-level definition of:

- `LlmConfig` — env-based LLM configuration.
- `NLQuestion` — the user's NL input.
- `GeneratedSql` — LLM output + metadata.
- `ValidationResult` — validation outcome.
- `QueryRow` — a dynamic result row (column→value pairs).
- `QueryResult` — executed result (SQL, rows, count, latency, error).
- `TextToSqlRequest` — full request (question + prompt + config).
- `TextToSqlResponse` — full response (question + SQL + validation + result/error).
- `SampleQuestion` — v1.1 sanity-check item.

## Validation Rules (enforced in the pipeline)

- **FR-013 / fail-fast**: If `FORGE_API_KEY` is missing, the pipeline raises a typed error before calling the LLM.
- **FR-006**: The `SqlValidator` rejects any SQL that is not a single SELECT on the `Orders` table with existing columns.
- **FR-007**: The `SqlValidator` rejects forbidden patterns (multi-statement, comments, blacklisted keywords).
- **FR-008**: The `TextToSqlResponse` always includes the generated SQL, whether accepted or rejected.
- **FR-011**: If the DB returns an error during execution, the `QueryResult.error` field captures it alongside the offending SQL.
- **FR-014** (v1.1): The pipeline logs each call (question, SQL, validation, result, latency).

## Boundary Enforcement

- `src/ai_engineering/llm_client.py` is the ONLY module that may import `openai` or `httpx`.
- `src/ai_engineering/` does NOT import `psycopg` or `pandas` (engine-specific code stays in adapters per constitution Principle III).
- `src/ai_engineering/` depends on `src/contracts/` (typed models) and the `QueryProvider` Protocol — it does NOT import `src/data_access/adapters/postgres/` directly.
- `tests/contract/test_boundaries.py` is extended to assert: (a) no `openai` import outside `ai_engineering/`, (b) no `psycopg` import outside `data_access/adapters/postgres/` (existing check), (c) all Text-to-SQL contract models are Pydantic v2 with explicit types.

## Out of Scope for This Contract

- **Semantic Layer with RBAC/RLS** — v2.0 scope (constitution Principle IV). `execute_readonly_query` does NOT enforce RLS; v2.0 will intercept it.
- **Multi-turn conversation context** — out of scope for v1.x; each `run(question)` call is independent.
- **Result-set comparison for evaluation** — v1.1 uses normalized SQL string comparison only (see research.md Part E).
