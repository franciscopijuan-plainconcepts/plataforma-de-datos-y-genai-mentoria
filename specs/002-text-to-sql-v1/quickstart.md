# Quickstart: Text-to-SQL v1.0 / v1.1

**Feature**: 002-text-to-sql-v1
**Date**: 2026-08-11
**Related**: [spec.md](./spec.md) · [plan.md](./plan.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/)

> Runnable validation guide proving the Text-to-SQL pipeline works end-to-end. Covers prerequisites, setup, validation commands, and expected outcomes. Implementation detail lives in `tasks.md`; this is a validation/run guide only.

## Prerequisites

- **Baseline (v0) must be running**: The warehouse must be bootstrapped and validated before any v1.x work. Verify:
  ```bash
  uv run python -m src.cli.main validate
  ```
  This MUST print `VALIDATION PASSED`. If it fails, run `bootstrap` first (see the baseline [quickstart.md](../001-data-genai-platform-baseline/quickstart.md)).

- **`FORGE_API_KEY` environment variable**: Set in `.env` (copy `.env.example` to `.env` and fill it). The Forge proxy endpoint and model name have defaults but the API key is required.

  ```bash
  cp .env.example .env
  # Edit .env and set FORGE_API_KEY=your-key-here
  ```

- **Network access to Forge**: The pipeline calls `https://forge.plainconcepts.com/v1`. Corporate network or VPN access is required.

## Setup (one-time, after baseline bootstrap)

```bash
# 1. Ensure dependencies are installed (openai + python-dotenv already in pyproject.toml)
uv sync

# 2. Ensure the baseline warehouse is running and validated
uv run python -m src.cli.main validate

# 3. Ensure .env has FORGE_API_KEY set
grep FORGE_API_KEY .env
```

**Expected outcome**: `uv sync` succeeds; `validate` prints `VALIDATION PASSED`; `FORGE_API_KEY` is non-empty in `.env`.

## Validation (proving v1.0 works)

### A. Ask a natural-language question

```bash
uv run python -m src.cli.main ask "What is the total sales amount?"
```

**Expected outcome** (spec FR-008 / SC-001 / SC-005):
- The system prints the **generated SQL** (e.g., `SELECT SUM("Sales") FROM Orders;`).
- The system prints the **result rows** (e.g., a single row with the total sales value).
- The response completes within 10 seconds (SC-001).

### B. Ask a Spanish-language question

```bash
uv run python -m src.cli.main ask "¿Cuál es el total de ventas por región?"
```

**Expected outcome** (FR-005):
- Generated SQL is a valid `SELECT ... GROUP BY` on the `Orders` table.
- Result rows show per-region totals.
- SQL references only existing `Orders` columns (no hallucinated names).

### C. Verify SQL transparency

```bash
uv run python -m src.cli.main ask "Show me the top 5 products by profit"
```

**Expected outcome** (FR-008 / SC-005):
- The output includes BOTH the SQL string AND the result rows.
- The SQL is a valid `SELECT ... ORDER BY ... LIMIT 5` on `Orders`.

### D. Verify failure handling (out-of-scope table)

```bash
uv run python -m src.cli.main ask "Show me all returned orders"
```

**Expected outcome** (FR-006 / SC-004):
- The LLM may generate SQL referencing `Returns` (out of v1.x scope).
- The system rejects the SQL with a clear message (e.g., "SQL references out-of-scope table: Returns. v1.x scope is Orders only.").
- No SQL is executed against the warehouse.

### E. Verify failure handling (non-SELECT)

> This is harder to trigger deterministically (depends on LLM output). Use the unit tests instead:

```bash
uv run pytest tests/unit/test_sql_validator.py -v
```

**Expected outcome** (FR-006 / SC-003):
- All SQL validator unit tests pass, confirming: INSERT/UPDATE/DELETE/DROP are rejected, multi-statement is rejected, non-`Orders` columns are rejected.

## Validation (proving v1.1 works)

### F. Run the sanity-check evaluation

```bash
uv run python -m src.cli.main evaluate
```

**Expected outcome** (FR-016 / FR-017 / SC-002):
- The system runs ~10 sample questions through the full pipeline.
- A simple summary is printed: `X / 10 correct`.
- Failed question IDs are listed.

> **Note**: The sanity check calls the real LLM (Forge), so it requires network access and a valid `FORGE_API_KEY`. It is not a unit test — it is an integration sanity check.

### G. Verify logging (v1.1)

After running `ask` or `evaluate`, check the structured log:

```bash
# The log location is configurable; default is .artifacts/text_to_sql.log
cat .artifacts/text_to_sql.log
```

**Expected outcome** (FR-014):
- Each call is logged with: input question, generated SQL, validation outcome, result/error, latency.

## Contract & Integration Tests

```bash
# Boundary contract tests (no LLM, no DB)
uv run pytest tests/contract/test_text_to_sql.py -v

# SQL validator unit tests (no LLM, no DB)
uv run pytest tests/unit/test_sql_validator.py -v

# End-to-end integration test (requires Docker PG + FORGE_API_KEY)
uv run pytest tests/integration/test_text_to_sql.py -v
```

**Expected outcome**:
- Contract tests: all pass (Text-to-SQL models are Pydantic v2; `openai` imports confined to `ai_engineering/`).
- Unit tests: all pass (validator rejects non-SELECT, non-Orders, non-existing-columns, forbidden patterns).
- Integration test: passes if the warehouse is running and `FORGE_API_KEY` is valid.

## What This Proves

- **Story 1 (P1)**: A user submits a natural-language question about `Orders` and receives typed results — validated via checks A, B, C.
- **Story 2 (P1)**: The generated SQL is returned alongside results, and rejected SQL is surfaced with a reason — validated via checks C, D.
- **Story 3 (P2)**: A simple sanity-check catches regressions — validated via check F.

## Out of Scope for this Quickstart

- **Semantic Layer / RBAC / RLS** — v2.0 scope.
- **Text-to-SQL on `Returns` or `People`** — v2.0 scope.
- **Model fine-tuning** — not in scope for v1.x.
- **Dashboard UI** — out of scope; CLI only.

See [spec.md § Out of Scope](./spec.md#out-of-scope) for the full list.
