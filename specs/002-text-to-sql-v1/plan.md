# Implementation Plan: Text-to-SQL v1.0 / v1.1

**Branch**: `002-text-to-sql-v1` | **Date**: 2026-08-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-text-to-sql-v1/spec.md`
**Related**: [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md)

## Summary

The v1.0 milestone delivers a natural-language-to-SQL pipeline over the `Orders` table: a user asks a question in Spanish or English, the system builds an LLM prompt enriched with semantic context from the existing `DataDictionaryDocument`, calls the LLM via the OpenAI Python SDK (Forge proxy), validates the generated SQL (SELECT-only, `Orders`-only, existing-columns-only), executes it through the reserved `QueryProvider` Protocol, and returns typed rows alongside the generated SQL. The v1.1 milestone adds a lightweight logging layer and a small (~10 question) sanity-check evaluation to catch obvious regressions — no precision-chasing or complex hardening.

The technical approach is deliberately minimal: no agent framework (LangChain/LangGraph), just a direct typed function-call flow, consistent with the constitution's YAGNI principle and strict-typing/layered-separation rules.

## Technical Context

**Language/Version**: Python 3.11+ (strict typing enforced — `mypy --strict` on `pyproject.toml`; pinned to 3.13 via `.python-version`).

**Primary Dependencies**:
- **`openai` (Python SDK, >=2.53.0)** — already in `pyproject.toml`. Used to call the LLM via the Forge proxy (`base_url="https://forge.plainconcepts.com/v1"`). This is the only LLM client; no LangChain/LangGraph.
- **`python-dotenv` (>=1.2.2)** — already in `pyproject.toml`. Loads `FORGE_API_KEY` from `.env`.
- **`pydantic` v2 (>=2.7)** — already in `pyproject.toml`. Used for all new AI Engineering contract models.
- **Existing baseline deps** — `psycopg`, `pandas`, `openpyxl` remain confined to their domains (no new cross-domain imports).

**Storage**: PostgreSQL 15 in Docker (existing baseline). No new storage. The `Orders` table is the query surface.

**Testing**: `pytest` (already configured). Contract tests for typed-boundary conformance + integration tests against the Dockerized PostgreSQL. The v1.1 sanity-check evaluation doubles as a lightweight integration test.

**Target Platform**: Linux/macOS/Windows local developer machine running Docker. No cloud, no web server.

**Project Type**: Library + CLI tooling (adds an `ai_engineering` domain and an `ask` CLI command to the existing baseline).

**Performance Goals**: End-to-end Text-to-SQL call (NL question -> result rows) completes within 10 seconds on a standard laptop (SC-001), including LLM round-trip via Forge.

**Constraints**: Offline-LLM not possible (Forge proxy requires network). Single-user, local CLI. No concurrent users. No auth. No governance (v2.0 scope).

**Scale/Scope**: One table (`Orders`, 51,290 rows). Single-LLM-call pipeline. ~10 sample evaluation questions. No multi-turn context.

**Open clarifications (deferred to Phase 0 research)**:
- Exact SQL validation strategy (regex vs. lightweight parser vs. `sqlglot` — research needed to pick the simplest approach that satisfies FR-006/FR-007 without over-engineering).
- How to serialize the `DataDictionaryDocument` into a prompt-efficient format (full dump vs. condensed — research needed to balance token cost vs. accuracy).
- Whether the existing `QueryProvider` Protocol needs a new method (e.g., `execute_readonly_query`) or whether the PG adapter can safely expose a read-only query path behind the Protocol without violating the "no `execute_sql` escape hatch" rule.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle (Constitution v1.0.0) | Status | v1.x plan compliance |
|---|---|---|
| I. Strictly-Typed Python Foundation | PASS | Python 3.11+; type hints on every signature; `mypy --strict` already configured; `Any` only with inline justification. New AI Engineering contract models are Pydantic v2. The OpenAI SDK client is wrapped behind a typed interface — no untyped `dict` crosses boundaries. |
| II. Layered Separation of Concerns (NON-NEGOTIABLE) | PASS | A new `src/ai_engineering/` domain is introduced for Text-to-SQL logic. Cross-domain traffic (AI Engineering -> Data Access) flows only through typed contracts in `src/contracts/` and the `QueryProvider` Protocol. No direct `psycopg`/`pandas` imports in `ai_engineering`. No upward dependencies (Data Engineering does not import AI Engineering). |
| III. Portable Data Access & Abstraction | PASS | The LLM-generated SQL is executed through the existing `QueryProvider` Protocol (reserved in the baseline for exactly this purpose). No raw `execute_sql` escape hatch is added to the shared interface. The PG adapter may implement a read-only query method behind the Protocol — engine-specific SQL execution stays confined to the adapter. |
| IV. Data Governance by Default (NON-NEGOTIABLE) | DEFERRED (v2.0) | RBAC/RLS is explicitly v2.0 scope. v1.x executes LLM-generated SQL against `Orders` without row-level security. The spec explicitly documents this gap and forbids claiming governance capabilities. This is a justified deferral, not a violation — the data model preserves the `Region` columns needed for future RLS. |
| V. Reproducible MLOps | PASS (minimal) | v1.x does not train models. The LLM model name, generation parameters, and prompt context are externalized via configuration, making each Text-to-SQL call reproducible (same input + same config -> same LLM call). The v1.1 sanity-check set provides a regression signal. Full MLflow/experiment-tracking is deferred until model fine-tuning (post-v2.0). |

**Gate status**: PASS. Principle IV is intentionally deferred to v2.0 per the spec's roadmap, with the data model kept governance-ready — this is a scoping decision, not a violation.

## Project Structure

### Documentation (this feature)

```text
specs/002-text-to-sql-v1/
├── plan.md              # This file (/speckit.plan output)
├── research.md          # Phase 0 output (SQL validation strategy, prompt design, QueryProvider extension)
├── data-model.md        # Phase 1 output (AI Engineering contract models)
├── quickstart.md        # Phase 1 output (runnable validation guide)
├── contracts/           # Phase 1 output (typed cross-boundary interfaces)
│   └── text_to_sql.md   # Text-to-SQL pipeline contracts
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── contracts/                     # Shared typed contracts (existing + new)
│   ├── data_access.py             # (existing) LogicalType, ColumnDef, TableDef, Row models
│   ├── ingestion.py               # (existing) EDA/ingestion contracts
│   ├── dictionary.py              # (existing) DataDictionaryDocument
│   └── text_to_sql.py             # (NEW) NLQuestion, GeneratedSql, ValidationResult, QueryResult,
│                                  #       TextToSqlRequest, TextToSqlResponse, SampleQuestion
├── data_access/                   # (existing) Engine-agnostic data-access layer
│   ├── interfaces.py              # (MODIFIED) QueryProvider Protocol gets execute_readonly_query method
│   └── adapters/postgres/         # (MODIFIED) repository implements execute_readonly_query
├── data_engineering/              # (existing, unchanged) EDA, ingestion, dictionary, validation
├── ai_engineering/                # (NEW) AI Engineering domain — Text-to-SQL pipeline
│   ├── __init__.py
│   ├── llm_client.py              # Typed wrapper around OpenAI SDK (Forge proxy, config from env)
│   ├── prompt_builder.py          # Builds LLM prompt from DataDictionaryDocument + NL question
│   ├── sql_validator.py           # Validates generated SQL (SELECT-only, Orders-only, columns-check)
│   ├── pipeline.py                # Orchestrates: prompt -> LLM -> parse -> validate -> execute -> return
│   └── evaluation.py              # (v1.1) Sanity-check evaluation harness (~10 sample questions)
└── cli/
    └── main.py                    # (MODIFIED) New `ask` command + `evaluate` command (v1.1)

tests/
├── contract/
│   ├── test_boundaries.py         # (MODIFIED) Assert no openai imports outside ai_engineering/
│   └── test_text_to_sql.py        # (NEW) Contract tests for Text-to-SQL models + validator
├── integration/
│   └── test_text_to_sql.py        # (NEW) End-to-end pipeline against Dockerized PG + Forge LLM
└── unit/
    └── test_sql_validator.py      # (NEW) Unit tests for SQL validation logic (no LLM, no DB)
```

**Structure Decision**: Single-project layout (continuing the baseline's Option 1). The new `src/ai_engineering/` domain is added alongside the existing `src/data_engineering/` and `src/data_access/` domains, preserving the constitution's layered separation. Cross-domain communication flows through `src/contracts/text_to_sql.py` (new) and the existing `QueryProvider` Protocol. The `openai` SDK import is confined to `src/ai_engineering/llm_client.py` — enforced by a boundary contract test. No framework dependencies are added.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. Table intentionally left empty — all constitutional principles are satisfied or are intentionally deferred per the spec's roadmap (Principle IV / Governance -> v2.0).
