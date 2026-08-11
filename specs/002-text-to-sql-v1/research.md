# Research: Text-to-SQL v1.0 / v1.1

**Phase**: 0 (Outline & Research)
**Feature**: 002-text-to-sql-v1
**Date**: 2026-08-11
**Status**: Complete — all NEEDS CLARIFICATION items resolved

> This document resolves the three open clarifications from `plan.md` Technical Context:
> 1. SQL validation strategy (regex vs. parser vs. `sqlglot`)
> 2. Prompt serialization of `DataDictionaryDocument`
> 3. `QueryProvider` Protocol extension for LLM-generated SQL execution

---

## Part A — SQL Validation Strategy

### Decision: Lightweight regex + keyword whitelist (no external SQL parser)

**Decision**: Use a simple, dependency-free validation approach combining:
1. A **keyword blacklist** (reject `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `GRANT`, `REVOKE`, `COPY`, `pg_sleep`, etc.).
2. A **single-statement check** (reject any SQL containing a semicolon followed by non-whitespace, or multiple statements).
3. A **comment rejection** (reject `--` and `/*` to prevent hidden clauses).
4. A **table whitelist** (the SQL may only reference the `Orders` table; reject `Returns`, `People`, or any other table name).
5. A **column whitelist** (the SQL may only reference columns that exist in the loaded `Orders` schema — derived from the `TableDef` / `DataDictionaryDocument`).

**Rationale**:
- The spec (FR-006/FR-007) requires validating SELECT-only, single-statement, Orders-only, existing-columns-only. This is a known, bounded problem for a single-table scope.
- `sqlglot` or `sqlparse` would add a dependency for a problem that regex + whitelist solves adequately at this scale. The constitution favors YAGNI; we are NOT building a general-purpose SQL firewall.
- The validation logic lives in `src/ai_engineering/sql_validator.py` (unit-testable without LLM or DB) and is tested exhaustively in `tests/unit/test_sql_validator.py`.
- This approach is intentionally **defensive, not cryptographic**. It catches common LLM failure modes (hallucinated columns, accidental multi-statement, non-SELECT output) but does not claim to be injection-proof. The spec explicitly states (FR-015) that complex injection-prevention is not required at this stage.

**Alternatives considered**:
- **`sqlglot` (Python SQL parser)** — powerful, but adds a dependency and parsing complexity for a single-table scope. Rejected for v1.x (may revisit if v2.0 Semantic Layer needs cross-table query analysis).
- **`sqlparse`** — lighter than sqlglot but still overkill for "is this a single SELECT on Orders?". Rejected.
- **PostgreSQL `EXPLAIN` as validation** — running `EXPLAIN` before execution would catch syntax errors but not semantic guardrails (e.g., a SELECT that references `Returns` would parse fine). Also requires a DB round-trip. Rejected as the sole mechanism (could be added as a secondary check in v1.1 if needed).

### Implementation detail

The validator receives:
- The raw SQL string from the LLM.
- An `OrdersSchema` snapshot (table name + allowed column names, derived from `TableDef`).

It returns a `ValidationResult` (accepted/rejected + reason). The checks run in order:
1. Strip leading/trailing whitespace.
2. Reject if empty or no recognizable `SELECT` keyword at the start.
3. Reject if any blacklisted keyword appears (case-insensitive).
4. Reject if `;` appears anywhere except possibly at the very end (single-statement enforcement).
5. Reject if `--` or `/*` appears (comment-based injection prevention).
6. Extract referenced identifiers (table names, column names) via simple regex tokenization; reject if any table is not `Orders` or any column is not in the whitelist.
7. If all pass, return accepted.

---

## Part B — Prompt Serialization of `DataDictionaryDocument`

### Decision: Condensed column-table format (not full Markdown dump)

**Decision**: Serialize the `DataDictionaryDocument` into a **condensed text block** for the LLM prompt, including:
- Table name + business purpose (1–2 sentences).
- A compact column list: `column_name | type | nullable | key | description` (one line per column, truncated descriptions to ~80 chars).
- The three cross-table relationships (one line each).
- Data-quality notes only for columns that have them (e.g., `Postal Code`: 80% NULL; `Profit`: signed).

**Rationale**:
- The full `data_dictionary.md` is ~5,000 tokens — too large for every prompt and mostly redundant (allowed values, min/max, unique counts are useful for humans but noise for the LLM).
- A condensed format (~500–800 tokens) gives the LLM the schema awareness it needs (column names, types, business meaning, relationships) without wasting tokens.
- The `prompt_builder.py` module is the ONLY place that serializes the `DataDictionaryDocument` — it takes the Pydantic model as input and produces a string. This keeps the serialization logic isolated and testable.

**Prompt structure** (v1.0):

```text
You are a data analyst assistant. Translate the user's natural-language question
into a single SQL SELECT query against the Orders table.

Database schema (Orders table — Transactional Logs):
- Row ID: INTEGER, NOT NULL, PRIMARY KEY. Unique identifier for each order line.
- Order ID: VARCHAR(50), NOT NULL. Identifier for the order (one order = multiple lines).
- Order Date: TIMESTAMP, NOT NULL. Date the order was placed.
- [... all 24 columns ...]
- Sales: NUMERIC(12,4), NOT NULL. Gross sales revenue for the line item.
- Profit: NUMERIC(12,4), NOT NULL, signed (negative = loss).
- [...]

Relationships:
- Returns.Order ID -> Orders.Order ID (a return refers to an order; out of scope for this query).
- People.Region -> Orders.Region (regional governance; out of scope for this query).

Rules:
- Output ONLY a single SQL SELECT statement. No explanations, no markdown.
- Query ONLY the Orders table. Do not reference Returns or People.
- Use only the columns listed above.

User question: {nl_question}
```

**Alternatives considered**:
- **Full `data_dictionary.md` dump** — too many tokens; most content (allowed values, unique counts, min/max) is not needed for SQL generation. Rejected.
- **Few-shot examples in the prompt** — could improve accuracy but adds prompt complexity and token count. Deferred to v1.1 if the sanity-check evaluation shows poor accuracy.
- **System message + separate schema message** — the OpenAI SDK supports multi-message prompting, but a single user message with the schema + question is simpler and sufficient for v1.0. Rejected for now; may revisit in v1.1.

---

## Part C — `QueryProvider` Protocol Extension

### Decision: Add `execute_readonly_query` method to `QueryProvider`

**Decision**: Extend the reserved `QueryProvider` Protocol (currently empty in `src/data_access/interfaces.py`) with a single method:

```python
@runtime_checkable
class QueryProvider(Protocol):
    def execute_readonly_query(self, sql: str, schema: TableDef) -> list[QueryRow]:
        """Execute a validated read-only SELECT query and return typed rows.

        The caller MUST validate the SQL before calling this method (the
        SqlValidator accepts only SELECT statements on the specified table).
        The adapter executes the query and maps each result row to a QueryRow
        model (typed dict-like Pydantic model with dynamic column keys).

        Args:
            sql: A validated single SELECT statement.
            schema: The TableDef of the queried table (for column mapping).

        Returns:
            A list of QueryRow models (typed, not raw dict).
        """
        ...
```

**Rationale**:
- The baseline's `contracts/data_access.md` explicitly reserved `QueryProvider` for "the future Text-to-SQL layer (v1.0/1.1)" and stated methods would be "semantic and always typed — never a raw `execute_sql(sql: str)` escape hatch."
- `execute_readonly_query` is semantically narrow: it accepts only validated SELECT SQL (the caller MUST run `SqlValidator` first) and returns typed `QueryRow` models, not raw `dict`. This is NOT a generic `execute_sql` escape hatch — it is a purpose-built read-only method behind a typed Protocol.
- The PG adapter implements it via `psycopg` cursor + `dict_row` factory → `QueryRow.model_validate(...)`. The future BigQuery adapter maps it to a BQ query job with the same typed output.
- The `schema: TableDef` parameter allows the adapter to map result columns back to typed models without introspecting the DB cursor (engine-neutral).
- The boundary contract test (`tests/contract/test_boundaries.py`) is extended to assert `openai` imports stay within `src/ai_engineering/`.

**Why not a raw `execute_sql(sql: str)`?**:
- The baseline's `research.md` Part C explicitly warns: "A generic `execute_sql(sql: str)` on `DataProvider`/`QueryProvider` would re-couple upstream code to PG-flavored SQL and silently break on BigQuery."
- `execute_readonly_query` avoids this by: (a) accepting only pre-validated SELECT SQL, (b) requiring the `TableDef` for typed mapping, (c) returning typed `QueryRow` models, not raw rows. The method name itself signals the read-only semantic contract.

**`QueryRow` model**: A Pydantic v2 model that holds arbitrary column→value pairs from a SELECT result (since Text-to-SQL results may be aggregations like `SUM(Sales)`, `COUNT(*)`, etc. — not full `OrderRow` instances). Defined in `src/contracts/text_to_sql.py`:

```python
class QueryRow(BaseModel):
    """A single row from a read-only query result, with dynamic columns."""
    data: dict[str, Any]  # column_name -> value (typed as Any at the boundary,
                          # but validated by the adapter before assignment)
```

> Note: `Any` is used here because Text-to-SQL results are inherently dynamic (aggregations, aliases, expressions). The constitution allows `Any` with inline justification — this is documented in the model's docstring. The adapter is responsible for coercing DB types to JSON-safe values before constructing the `QueryRow`.

**Alternatives considered**:
- **Return `list[OrderRow]`** — too restrictive; Text-to-SQL queries are often aggregations (`SELECT region, SUM(sales) FROM orders GROUP BY region`) that don't map to `OrderRow`. Rejected.
- **Add `execute_sql(sql: str)` to `DataProvider`** — violates the baseline's explicit design rule and re-couples to engine SQL. Rejected.
- **Execute SQL in the AI Engineering domain directly (import `psycopg`)** — violates constitution Principle III (engine-specific code outside adapters) and Principle II (cross-domain internal imports). Rejected.

---

## Part D — LLM Client Configuration

### Decision: Typed wrapper around OpenAI SDK with env-based config

**Decision**: `src/ai_engineering/llm_client.py` wraps the OpenAI Python SDK behind a typed interface:

```python
class LlmClient:
    def __init__(self, config: LlmConfig) -> None:
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            http_client=httpx.Client(verify=False),  # Forge proxy SSL (as in test.ipynb)
        )

    def generate_sql(self, prompt: str) -> GeneratedSql:
        response = self._client.chat.completions.create(
            model=config.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
        return GeneratedSql(
            sql=response.choices[0].message.content or "",
            model_name=config.model_name,
            raw_response=response.model_dump(),
        )
```

**Config** (loaded from env via `python-dotenv`):

| Env var | Purpose | Default |
|---|---|---|
| `FORGE_API_KEY` | LLM API key (required, fail-fast if missing — FR-013) | — |
| `FORGE_BASE_URL` | LLM endpoint | `https://forge.plainconcepts.com/v1` |
| `FORGE_MODEL_NAME` | Model name | `glm-5-2` |
| `FORGE_MAX_TOKENS` | Max generation tokens | `4096` |
| `FORGE_TEMPERATURE` | Sampling temperature | `0.0` (deterministic for SQL) |

**Rationale**:
- The user's `test.ipynb` already validated this exact pattern (OpenAI client + Forge `base_url` + `httpx.Client(verify=False)`). Reusing it minimizes risk.
- Temperature `0.0` for SQL generation: we want deterministic, reproducible output (constitution Principle V — reproducibility).
- The `httpx.Client(verify=False)` is required because the Forge proxy uses a corporate SSL certificate that the default httpx trust store doesn't recognize. This is a known corporate-environment workaround.
- The `openai` import is confined to `src/ai_engineering/llm_client.py` — enforced by the boundary contract test.

**Alternatives considered**:
- **`httpx.Client(verify=True)` with a custom CA bundle** — more correct, but requires the user to configure the corporate root CA. Deferred (the test notebook already uses `verify=False`).
- **Async OpenAI client** — no benefit for a single-user CLI. Rejected (same rationale as the baseline's sync `psycopg` decision).

---

## Part E — Sanity-Check Evaluation Design (v1.1)

### Decision: Simple JSON file + pass/fail summary

**Decision**: The v1.1 sanity-check evaluation uses:
- A `sample_questions.json` file (committed in `specs/002-text-to-sql-v1/`) with ~10 questions, each containing: the NL question + the expected SQL (string comparison, not result-set comparison, to keep it simple).
- A `evaluate` CLI command that runs each question through the full pipeline and compares the generated SQL to the expected SQL (normalized: lowercase, whitespace-collapsed).
- A simple summary: `X / 10 correct` + list of failed question IDs.

**Rationale**:
- The spec (FR-016/FR-017) explicitly asks for a "simple" sanity check — "no per-failure diagnostics or configurable comparison modes."
- SQL string comparison (normalized) is simpler than result-set comparison (which requires executing both queries and comparing rows). It's not perfect (semantically equivalent SQL with different syntax would fail), but the goal is regression detection, not precision.
- ~10 questions cover: simple SELECT, aggregation (SUM/AVG), GROUP BY, top-N (ORDER BY + LIMIT), WHERE filter, date range, COUNT, and multilingual (Spanish + English).

**Sample question structure**:

```json
[
  {
    "id": "q01",
    "question": "What is the total sales amount?",
    "expected_sql_normalized": "select sum(sales) from orders"
  },
  {
    "id": "q02",
    "question": "¿Cuál es el total de ventas por región?",
    "expected_sql_normalized": "select region, sum(sales) from orders group by region"
  }
]
```

**Alternatives considered**:
- **Result-set comparison** — more accurate but requires executing the expected SQL and comparing rows (ordering, types, rounding). Overkill for a sanity check. Rejected.
- **Golden question set with 20+ questions** — the spec was simplified to ~10. Rejected (user explicitly asked for simpler).

---

## Resolution Summary (NEEDS CLARIFICATION items closed)

| Open clarification (from plan.md Technical Context) | Resolution |
|---|---|
| SQL validation strategy | **Lightweight regex + keyword/column/table whitelist** in `sql_validator.py` — no external parser dependency |
| Prompt serialization of `DataDictionaryDocument` | **Condensed column-table format** (~500–800 tokens) built by `prompt_builder.py` |
| `QueryProvider` Protocol extension | **Add `execute_readonly_query(sql, schema) -> list[QueryRow]`** — typed, read-only, not a generic escape hatch |
| LLM client configuration | **Typed `LlmClient` wrapper** with env-based config (`FORGE_API_KEY`, `FORGE_BASE_URL`, `FORGE_MODEL_NAME`, etc.) |
| Sanity-check evaluation design | **JSON file + normalized SQL string comparison + simple pass/fail summary** |

All NEEDS CLARIFICATION items are resolved. Phase 1 design (data-model.md, contracts/, quickstart.md) can proceed.
