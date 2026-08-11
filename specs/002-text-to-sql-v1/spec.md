# Feature Specification: Text-to-SQL v1.0 / v1.1

**Feature Branch**: `002-text-to-sql-v1`

**Created**: 2026-08-04

**Status**: Draft

**Input**: User description: "Agregar la feature siguiente: v1.0 capa inicial de Text-to-SQL sobre Orders; v1.1 mejoras de robustez de Text-to-SQL (validaciones, hardening, evaluacion). Se debe revisar si ya tenemos una capa semantica que el modelo pueda aprovechar para hacer bien las consultas. El modelo se debe usar con el cliente de OpenAI. Habria que evaluar si usar un framework de agentes (LangChain, LangGraph) o algo mas simple."

## Scope Summary

This specification defines the **v1.0 and v1.1** milestones of the Plataforma de Datos y GenAI: a natural-language interface that translates user questions into SQL queries executed against the `Orders` table of the local PostgreSQL warehouse, returning typed results to the user.

It builds on the baseline (v0, feature `001-data-genai-platform-baseline`) which delivered a populated PostgreSQL warehouse with the `Orders`, `Returns`, and `People` tables plus a comprehensive data dictionary.

### v1.0 — Initial Text-to-SQL (MVP)

- A natural-language question in Spanish or English is translated into a SQL query against the `Orders` table.
- The LLM is provided with semantic context derived from the existing data dictionary and data model (tables, columns, business descriptions, types, relationships) — NOT a formal Semantic Layer (which is v2.0 scope per the constitution).
- The generated SQL is validated (basic guardrails: SELECT-only, single-statement, no destructive operations) before execution.
- Results are returned as typed rows via the existing `QueryProvider` Protocol hook.

### v1.1 — Hardening

- Strengthened SQL validation (allowed-columns whitelist, injection-prevention, query-complexity limits).
- A typed evaluation harness with a curated golden-question set to measure translation accuracy.
- Error handling, logging, and observability of each Text-to-SQL call (input NL, generated SQL, execution result, latency).

### Explicitly out of scope

- **Semantic Layer with RBAC/RLS** — v2.0 scope (constitution Principle IV, NON-NEGOTIABLE). No governance logic is introduced in v1.x; the LLM-generated SQL runs against `Orders` without row-level security. The spec explicitly documents this gap and forbids claiming governance capabilities.
- **Text-to-SQL on `Returns` or `People`** — v1.x scope is `Orders` only (per baseline spec). `Returns` and `People` are reserved for v2.0 business-logic and RLS work.
- **Dashboard UI** — out of scope; this feature is a query interface (CLI / typed API), not a visualization layer.
- **Model fine-tuning** — not in scope for v1.x; out-of-the-box LLM prompting is used.
- **Cloud deployment (BigQuery)** — out of scope; local PostgreSQL only.

## Semantic Layer Assessment (critical context)

The user asked: "do we already have a semantic layer the model can leverage?"

**Answer: No formal Semantic Layer exists yet.** A full Semantic Layer with RBAC/RLS is constitutionally deferred to v2.0 (Principle IV). However, the baseline (v0) produced rich **semantic context** that the LLM CAN and SHOULD leverage as prompt context:

| Artifact | Location | What it provides the LLM |
| --- | --- | --- |
| Data dictionary (rendered) | `data_dictionary.md` | Column names, business descriptions, types, nullability, keys, allowed values, data-quality notes, cross-table relationships — all in human-readable Markdown |
| Data dictionary (structured) | `src/contracts/dictionary.py` (`DataDictionaryDocument`) | The same content as a Pydantic model, suitable for serializing into a prompt |
| Data model | `specs/001-data-genai-platform-baseline/data-model.md` | Entity relationships, validation rules, PK/FK structure |
| Kaggle semantic source | `src/data_engineering/dictionary/semantic_source.py` | Curated business descriptions (Orders = Transactional Logs; each column's meaning) |

**Decision for v1.x**: The LLM prompt context will be built from the `DataDictionaryDocument` (the structured Pydantic model from the baseline). This gives the LLM accurate business semantics without requiring a v2.0 Semantic Layer. This is an interim approach; v2.0 will replace it with a governed Semantic Layer that also enforces RBAC/RLS.

## Framework Decision (critical context)

The user asked: "should we use an agent framework like LangChain / LangGraph, or something simpler?"

**Decision: Start simple — use the OpenAI Python SDK directly, no agent framework for v1.0.**

**Rationale**:
- The constitution favors simplicity (YAGNI) within its constraints.
- v1.0 is fundamentally a single-LLM-call pipeline: NL question + semantic context → SQL → validate → execute → return rows. This is a function call, not a multi-step agent graph.
- The user already validated the OpenAI client against the Forge proxy in their notebook (`test.ipynb`); reusing that exact pattern keeps the stack minimal.
- LangChain/LangGraph add abstraction layers, dependency weight, and implicit control flow that obscure typing boundaries — at odds with the constitution's strict-typing and layered-separation principles.
- **v1.1 may revisit** if hardening reveals a genuine need for multi-step orchestration (e.g., self-correction loops, multi-turn context). But the spec does not pre-commit to a framework; it commits to revisiting the decision only if warranted.

**Re-evaluation trigger for v1.1**: If the golden-set evaluation shows that single-pass translation accuracy is below target AND self-correction loops would materially improve it, a lightweight orchestration layer MAY be introduced in v1.1 — but it MUST live behind typed contracts and MUST NOT couple the AI Engineering domain to framework internals at the Protocol boundary.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Translate a natural-language question into SQL and get results (Priority: P1) 🎯 MVP

An analyst or developer asks a question in natural language (Spanish or English) about the `Orders` data — e.g., "¿Cuál es el total de ventas por región?" or "Show me the top 5 products by profit" — and receives the query results as typed rows, without writing SQL themselves.

**Why this priority**: This is the core deliverable of v1.0. It proves the end-to-end Text-to-SQL pipeline works: NL input → semantic-context-augmented LLM → validated SQL → executed against the warehouse → typed result returned. Without this, there is no Text-to-SQL feature.

**Independent Test**: Ask a supported question via the CLI (or typed API), confirm a non-empty result set is returned as typed `OrderRow`-compatible rows, and the generated SQL is valid PostgreSQL that executes successfully against the `Orders` table.

**Acceptance Scenarios**:

1. **Given** the baseline warehouse is running and populated, **When** a user submits a natural-language question about `Orders` (e.g., "total sales by region"), **Then** the system generates a valid SQL query, executes it, and returns the result as typed rows.
2. **Given** a valid question, **When** the system builds the LLM prompt, **Then** the prompt includes semantic context from the data dictionary (table name, column names, business descriptions, types, relationships) so the LLM has accurate schema awareness.
3. **Given** the LLM generates SQL, **When** the SQL is validated, **Then** only SELECT statements against the `Orders` table are accepted; any INSERT/UPDATE/DELETE/DROP or multi-statement query is rejected before execution.
4. **Given** the LLM returns a malformed or non-SQL response, **When** the system attempts to parse it, **Then** a clear error is returned to the user (no silent failure, no partial execution).
5. **Given** a successful query, **When** the result is returned, **Then** the caller receives typed rows (not raw `dict` or untyped payloads), preserving the constitution's typed-boundary rule.

---

### User Story 2 — Understand and trust the generated SQL (Priority: P1)

A user wants to see the SQL that was generated for their question before or alongside the results, so they can verify correctness and understand what was executed against the warehouse.

**Why this priority**: Without transparency, Text-to-SQL is a black box that users cannot trust. Showing the generated SQL is a prerequisite for adoption, debugging, and the v1.1 evaluation harness.

**Independent Test**: Submit a question and confirm the system returns both the generated SQL string AND the executed result. The SQL returned MUST be the exact query that was executed (no post-hoc modification).

**Acceptance Scenarios**:

1. **Given** a successful Text-to-SQL call, **When** the result is returned, **Then** the response includes the generated SQL string alongside the result rows.
2. **Given** a validation-rejected SQL, **When** the system rejects it, **Then** the response includes the rejected SQL AND the reason for rejection (e.g., "contains forbidden keyword: DROP").
3. **Given** a successful call, **When** the user reviews the SQL, **Then** the SQL references only existing `Orders` columns (no hallucinated column names) — validated against the schema.

---

### User Story 3 — Run a basic sanity-check over Text-to-SQL (Priority: P2 — v1.1)

A developer wants a quick, repeatable way to confirm the pipeline is not broken across a handful of common questions — not to chase precision, just to catch obvious regressions.

**Why this priority**: P2 because v1.0 already proves the pipeline works end-to-end. v1.1 adds only a lightweight sanity check so a refactor or prompt change does not silently break common queries.

**Independent Test**: Run the evaluation command against a small set (~10 questions) and confirm a simple pass/fail summary is printed.

**Acceptance Scenarios**:

1. **Given** a small set of sample questions with expected results, **When** the evaluation runs, **Then** each question is executed and marked pass/fail, and a simple summary (correct count / total) is printed.

---

### Edge Cases

- **What happens when the LLM generates SQL referencing a non-existent column?** The validator MUST reject it with a clear error listing the known `Orders` columns, before any execution attempt.
- **What happens when the LLM generates SQL referencing `Returns` or `People` tables (out of v1.x scope)?** The validator MUST reject it; v1.x scope is `Orders` only. The error message should explain that other tables are v2.0 scope.
- **What happens when the LLM generates a non-SELECT statement (INSERT, UPDATE, DROP, etc.)?** The validator MUST reject it before execution — no destructive SQL is ever executed.
- **What happens when the LLM returns non-SQL text (e.g., a conversational answer)?** The system MUST detect the absence of a parseable SQL statement and return a clear error, not attempt to execute prose.
- **What happens when the Forge/proxy endpoint is unreachable or returns an error?** The system MUST surface a clear error (connection failed, auth error, rate-limited) and NEVER silently fall back to a default query.
- **What happens when the generated SQL times out or errors at execution?** The system MUST capture the database error and return it to the user with the offending SQL, without crashing.
- **What happens when the API key is missing or invalid?** The system MUST fail fast with a clear actionable error (like the baseline's FR-013 pattern), pointing to the required environment variable.

## Requirements *(mandatory)*

### Functional Requirements

#### LLM Integration (v1.0)

- **FR-001**: The system MUST integrate with an LLM via the OpenAI Python SDK, using a configurable base URL and API key (to support the Forge proxy in the corporate environment), loaded from environment variables.
- **FR-002**: The system MUST NOT use an agent framework (LangChain, LangGraph, etc.) for v1.0; the pipeline MUST be a direct, typed function-call flow: build prompt → call LLM → parse SQL → validate → execute → return.
- **FR-003**: The model name and generation parameters (max tokens, temperature) MUST be externalized via configuration, not hardcoded.
- **FR-004**: The system MUST inject semantic context into the LLM prompt, built from the existing `DataDictionaryDocument` (table name, column names, business descriptions, types, nullability, key flags, relationships). This serves as the interim "semantic layer" until v2.0.

#### SQL Generation & Validation (v1.0)

- **FR-005**: The system MUST translate a natural-language question (Spanish or English) into a SQL query targeting the `Orders` table only.
- **FR-006**: The system MUST validate the generated SQL before execution: (a) single-statement only, (b) SELECT-only (reject INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/etc.), (c) references only the `Orders` table (reject `Returns`/`People`), (d) references only existing `Orders` columns.
- **FR-007**: The system MUST reject SQL that contains forbidden patterns (e.g., multiple statements separated by semicolons, comments used to hide clauses, `pg_sleep`, `COPY`, `CREATE`) before execution.
- **FR-008**: The system MUST return both the generated SQL AND the executed result to the caller, preserving transparency.

#### Query Execution (v1.0)

- **FR-009**: The system MUST execute validated SQL through the existing data-access layer — specifically through the `QueryProvider` Protocol (reserved in the baseline for exactly this purpose), NOT through a raw `execute_sql` escape hatch.
- **FR-010**: Results MUST be returned as typed models (consistent with the constitution's typed-boundary rule) — never raw `dict` or untyped DBAPI rows.
- **FR-011**: The system MUST surface execution errors (timeouts, SQL errors) clearly, including the offending SQL and the database error message.

#### CLI Interface (v1.0)

- **FR-012**: The system MUST provide a CLI command (e.g., `uv run python -m src.cli.main ask <question>`) that accepts a natural-language question and prints the generated SQL and the result rows.
- **FR-013**: The system MUST fail fast with a clear actionable error when the Forge API key is missing, the warehouse is not running, or the baseline is not validated.

#### Hardening (v1.1 — lightweight)

- **FR-014**: The system MUST log each Text-to-SQL call (input question, generated SQL, validation outcome, result/error, latency) to a simple structured log for basic observability.
- **FR-015**: The system MAY add a result-row cap to prevent runaway queries. No complex injection-prevention or query-complexity analysis is required at this stage.

#### Sanity-Check Evaluation (v1.1 — lightweight)

- **FR-016**: The system MUST provide a small set of sample questions (~10) with expected results covering common `Orders` query patterns (aggregations, filters, top-N).
- **FR-017**: The system MUST provide a simple evaluation command that runs the sample set and prints a pass/fail summary (correct count / total). No per-failure diagnostics or configurable comparison modes are required.

### Key Entities

- **NLQuestion**: The user's natural-language input (string).
- **TextToSqlRequest**: A typed model containing the NL question + the semantic context snapshot used.
- **GeneratedSql**: The SQL string produced by the LLM, plus metadata (model name, raw LLM response).
- **ValidationResult**: The outcome of validating the generated SQL (accepted/rejected + reason).
- **QueryResult**: The executed result — typed rows + the SQL that was executed + execution metadata (row count, latency).
- **TextToSqlResponse**: The full typed response returned to the caller: NL question, generated SQL, validation result, query result (or execution error).
- **SampleQuestion**: A sample evaluation item: NL question + expected result set (used only for the v1.1 sanity check).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can submit a natural-language question about the `Orders` table and receive typed query results within 10 seconds on a standard developer laptop (including LLM round-trip).
- **SC-002**: The sanity-check evaluation (v1.1) runs a small set of sample questions and prints a simple pass/fail summary.
- **SC-003**: The system rejects 100% of non-SELECT or destructive SQL before execution (measured by the validation test suite).
- **SC-004**: The system rejects 100% of SQL referencing non-existent `Orders` columns or out-of-scope tables (`Returns`/`People`) before execution.
- **SC-005**: Each Text-to-SQL call returns the generated SQL alongside the result, enabling a reviewer to verify what was executed.
- **SC-006**: A contributor can run the Text-to-SQL pipeline from a clean clone (after baseline `bootstrap`) with one environment variable (`FORGE_API_KEY`) and one CLI command.

## Assumptions

- **Baseline is a prerequisite**: The v0 baseline (`001-data-genai-platform-baseline`) MUST be completed and the warehouse running before any v1.x work. Text-to-SQL depends on the populated `Orders` table and the `DataDictionaryDocument`.
- **LLM via Forge proxy**: The corporate Forge endpoint (`https://forge.plainconcepts.com/v1`) is the LLM provider, accessed via the OpenAI Python SDK with a custom `base_url`. The model `glm-5-2` (or whatever Forge exposes) is the default. The user's `test.ipynb` notebook validated this pattern.
- **API key handling**: `FORGE_API_KEY` is stored in `.env` (gitignored, per constitution) and loaded via `python-dotenv` (already a project dependency). The system never logs the key.
- **No formal Semantic Layer**: v1.x uses the `DataDictionaryDocument` as interim semantic context. This is NOT a Semantic Layer (no RBAC/RLS, no metric definitions, no governance). This is explicitly documented as an interim approach; v2.0 replaces it.
- **No governance**: Per constitution Principle IV, v1.x does NOT enforce RBAC/RLS. The LLM-generated SQL executes against `Orders` without row-level scoping. The system MUST NOT claim governance capabilities. This is a known, documented gap until v2.0.
- **Language**: Both Spanish and English natural-language questions are supported (the dataset and team are Spanish-speaking; the LLM handles multilingual input). The generated SQL is always standard SQL.
- **Single-user, local**: The pipeline runs locally as a CLI/typed-API. No concurrent users, no auth, no web server — consistent with the baseline's local-CLI scope.
- **Existing Protocol**: The `QueryProvider` Protocol (reserved in `src/data_access/interfaces.py` during the baseline) is the hook for v1.x query execution. No raw `execute_sql` is added.
- **New domain**: Per constitution Principle II, Text-to-SQL logic lives in a new `src/ai_engineering/` domain (AI Engineering), separate from `src/data_engineering/`. Cross-domain traffic flows through typed contracts in `src/contracts/`.
- **Framework re-evaluation in v1.1**: The "no framework" decision is for v1.0. If v1.1 evaluation reveals that multi-step orchestration (self-correction, tool-use) materially improves accuracy, a lightweight orchestration layer MAY be introduced — behind typed contracts, never leaking framework internals across boundaries.
- **Dependencies**: The baseline already added `openai` and `python-dotenv` to `pyproject.toml`. No new heavy dependencies are introduced for v1.0.

## Out of Scope

- **Semantic Layer / RBAC / RLS** — v2.0 (constitution Principle IV, NON-NEGOTIABLE).
- **Text-to-SQL on `Returns` or `People`** — v2.0 (business logic + RLS on those tables).
- **Model fine-tuning** — not in scope for v1.x; out-of-the-box prompting only.
- **Dashboard / web UI** — out of scope; CLI + typed API only.
- **Multi-turn conversation context** — out of scope for v1.0; each question is independent. May be revisited in v1.1 only if warranted by evaluation.
- **Cloud deployment / BigQuery** — out of scope; local PostgreSQL only.
- **Multi-user / authentication** — out of scope; local single-user CLI.
