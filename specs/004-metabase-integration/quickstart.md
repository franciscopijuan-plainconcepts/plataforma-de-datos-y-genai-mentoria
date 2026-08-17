# Quickstart: Metabase Integration (Governed SQL Cards from Chat Sessions)

**Feature**: 004-metabase-integration
**Date**: 2026-08-17
**Related**: [spec.md](./spec.md) · [plan.md](./plan.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/)

> Runnable validation guide de la integración con Metabase v2.1. Cubre: setup automatizado, integración con `ask` (--no-metabase, --session), governance NON-NEGOTIABLE check (re-ejecutar una card desde Metabase devuelve solo el scope del viewer), y CLI operations. Es una guía de validación — los detalles de implementación viven en `tasks.md`.

## Prerequisites

- **Features 001, 002, 003 completas y mergeadas a `main`**: warehouse cargado, Text-to-SQL pipeline funcionando, Semantic Layer + RLS enforced (`GovernedQueryProvider` activo).
- **Docker running**: igual que las features anteriores.
- **`.env` con `FORGE_API_KEY`**: igual que en 002 y 003.
- **`.env` con `METABASE_*` vars nuevas**:
  ```bash
  METABASE_HOST=http://localhost:3000
  METABASE_PORT=3000
  METABASE_ADMIN_EMAIL=admin@plataforma.local
  METABASE_ADMIN_PASSWORD=metabase_dev  # local-only; .env is gitignored
  ```
  Ver `.env.example` (actualizado por esta feature).

## Setup (one-time, after PG bootstrap)

### 1. Pull de la nueva imagen de Metabase y添加剂 dependencies

```bash
uv sync  # ensures httpx (already transitivo vía openai)
docker compose -f docker/docker-compose.yml pull metabase
```

**Expected**: `uv sync` succeeds; the metabase image pulls OK.

### 2. Levantar Metabase + configurar via REST API

```bash
uv run python -m src.cli.main metabase setup
```

**Expected outcome** (spec FR-003 / SC-001 / SC-002):
- El CLI trae arriba el container de Metabase en Docker (si no está corriendo).
- Espera el healthcheck de Metabase (`GET /api/health`).
- Via REST API:
  - `GET /api/session/properties` → check if setup already done; if not →
  - `POST /api/setup` → creates the admin user (idempotente).
  - `POST /api/database` → connects Metabase to PostgreSQL using the `metabase_readonly` role.
  - `POST /api/collection` → creates the "Chat Sessions" collection.
- The CLI writes `.artifacts/metabase_state.json` with the persisted state.
- The CLI prints a summary: admin_user, db_id, collection_id, version.

### 3. Verificar setup en la UI

Open `http://localhost:3000` in a browser:
- Login with `admin@plataforma.local` / `metabase_dev`.
- Navigate to **Browse Data** → see PostgreSQL listed as a database.
- Navigate to **Collections** → see "Chat Sessions" exists.

**Expected** (spec SC-002): no setup wizard present, PostgreSQL connected, "Chat Sessions" collection exists.

### 4. Idempotency check

```bash
uv run python -m src.cli.main metabase setup  # second run
```

**Expected** (spec FR-004): the CLI detects setup is already complete via `is_setup_complete()` and skips re-creation of the admin user, DB connection, and collection. Prints "already configured".

## Validation — A. End-to-end ask → card

### A1. Ask genera una card en Metabase automatically

```bash
ENV=local uv run python -m src.cli.main ask --viewer marilene_rousseau "total sales by region"
```

**Expected** (spec FR-010 / FR-012 / SC-004):
- The `ask` runs normally (logs in person, generates SQL, applies RLS via `GovernedQueryProvider`, executes, returns typed rows).
- At the end, it prints `Metabase card created: id=<N> name='...'`.
- Open Metabase → "Chat Sessions" collection → a new card appears with:
  - `name`: derived from the question (e.g., "Total Sales By Region")
  - `display`: `bar` (GROUP BY region, ≤20 results)
  - `description`: includes `viewer_id=marilene_rousseau` and timestamp.
  - SQL: the **governed SQL** (includes `WHERE "Region" IN ('Caribbean')`).

### A2. Governance NON-NEGOTIABLE check (constitution Principle IV)

```bash
# Get the card ID from A1 (or use the Metabase UI).
CARD_ID=N
# Re-execute the card via Metabase API and check the result region values.
curl -s \
  -H "Content-Type: application/json" \
  -X POST \
  -d "{\"database\":1,\"parameters\":[],\"type\":\"native\",\"native\":{\"query\":\"<the card SQL>\"}}" \
  -H "X-Metabase-Session: <session_token>" \
  http://localhost:3000/api/dataset

# Simplified user check: just open the card from the Metabase UI.
```

Open the card in Metabase UI → click "Refresh" or just visualize the chart:

**Expected** (spec SC-003): all rows in the chart have `"Region" = 'Caribbean'` (or `Marilène Rousseau`'s scoped region). NO data from other regions appears. This proves the governance is preserved even when Metabase re-executes the card — the SQL is already governed.

### A3. --no-metabase flag does NOT create a card

```bash
ENV=local uv run python -m src.cli.main ask --viewer marilene_rousseau "total sales" --no-metabase
```

**Expected** (spec FR-014 / SC-006):
- The `ask` runs normally, returns rows.
- NO message about Metabase card creation.
- Open Metabase → no new card has been added since A1.

### A4. Best-effort when Metabase is down

```bash
uv run python -m src.cli.main metabase teardown  # stop Metabase
ENV=local uv run python -m src.cli.main ask --viewer marilene_rousseau "total sales"
```

**Expected** (spec FR-013 / SC-007):
- The `ask` runs normally, returns rows.
- A warning logged to `.artifacts/text_to_sql.log`: `metabase_status=failed`.
- The user output does NOT show Metabase card creation.

## Validation — B. Sessions (US3)

### B1. Group multiple asks under a session dashboard

```bash
ENV=local uv run python -m src.cli.main ask --viewer marilene_rousseau --session my-bi-review "top 5 customers by sales"
ENV=local uv run python -m src.cli.main ask --viewer marilene_rousseau --session my-bi-review "orders by segment"
ENV=local uv run python -m src.cli.main ask --viewer marilene_rousseau --session my-bi-review "profit by category"
```

**Expected** (spec FR-015 / FR-016 / SC-005):
- 3 cards created in the "Chat Sessions" collection.
- Open Metabase → navigate to the dashboard named `"Session: my-bi-review"`.
- All 3 cards are visible there in sequence positions.

### B2. Different viewers in the same session

```bash
ENV=local uv run python -m src.cli.main ask --viewer marilene_rousseau --session mixed-viewers "sales summary"
ENV=local uv run python -m src.cli.main ask --viewer flannery_newton --session mixed-viewers "sales summary"
```

**Expected** (spec edge case "viewer changes between two asks in a same session"):
- 2 cards in the same "Session: mixed-viewers" dashboard.
- Card 1 SQL includes `WHERE "Region" IN ('Caribbean')` (Marilène's region).
- Card 2 SQL includes `WHERE "Region" IN ('Southern US')` (Flannery's region).
- Each card's `description` records its own `viewer_id` for auditoria.

## Validation — C. CLI Operations (US4)

### C1. `metabase status`

```bash
uv run python -m src.cli.main metabase status
```

**Expected** (spec FR-018 / SC-008): prints container status, Metabase version, DB connection status (yes/no), number of cards in the "Chat Sessions" collection, admin user email.

### C2. `metabase reset-cards`

```bash
uv run python -m src.cli.main metabase status | grep -E "cards"
uv run python -m src.cli.main metabase reset-cards
uv run python -m src.cli.main metabase status | grep -E "cards"
```

**Expected** (spec FR-020): cards count goes to 0 after reset-cards; the admin user and DB connection remain intact (only the cards in "Chat Sessions" are deleted).

### C3. `metabase teardown`

```bash
uv run python -m src.cli.main metabase teardown --remove-volume
docker ps | grep metabase  # should be empty
```

**Expected** (spec FR-019): the Metabase container is stopped and removed; with `--remove-volume`, the `plataforma_metabase_data` volume is also removed ( wiping the admin user, cards, and config — next `metabase setup` starts fresh).

## Validation — D. Boundary tests

### D1. Boundary tests extended

```bash
uv run pytest tests/contract/test_boundaries.py -v
```

**Expected**: the new `test_httpx_confined_to_metabase_client` pass — no module outside `src/ai_engineering/metabase_client.py` imports `httpx`. The existing boundaries (`openai`/`httpx` from feature 002/003 stays in `ai_engineering/llm_client.py`, `psycopg` in `data_access/adapters/postgres/`, `pyyaml` in `data_engineering/semantic_layer/registry.py`) still pass.

Wait — `httpx` is now imported in two places (`llm_client.py` from feature 002 and `metabase_client.py` from feature 004). The boundary check in 002 said "httpx only in `ai_engineering`" (broadly). Feature 004 scopes it: the new test asserts `httpx` imports stay within `src/ai_engineering/` (both `llm_client.py` and `metabase_client.py` are OK; anywhere else is not).

Run all contract tests:

```bash
uv run pytest tests/contract/ -v
```

### D2. Unit tests for `MetabaseClient` (no Metabase required)

```bash
uv run pytest tests/unit/test_metabase_client.py -v
```

**Expected**: tests use a fake `httpx.MockTransport` to simulate API responses; no Docker Metabase required.

### D3. Integration test end-to-end

```bash
uv run pytest tests/integration/test_metabase_integration.py -v
```

This test requires:
- Docker Metabase up and setup complete (`metabase setup`).
- `FORGE_API_KEY` set.
- `ENV=local`.
- A valid `viewers.yaml` defining `marilene_rousseau`.

The test will be SKIPPED without these preconditions (constitution allows integration tests against Docker; we don't mock).

**Expected** (spec SC-003, NON-NEGOTIABLE): a single `ask --viewer marilene_rousseau "total sales"` creates a card; the test re-executes the card via Metabase API; the result rows have `"Region" = 'Caribbean'` exclusively. NO rows from other regions. The governance NON-NEGOTIABLE check passes.

## Validation — E. Type checking strictness

### E1. `mypy --strict`

```bash
uv run mypy --strict src/ tests/
```

**Expected** (spec SC-010): zero errors. All new models in `src/contracts/metabase.py` are Pydantic v2 with explicit types. `httpx` stays confined (mypy override may be needed for httpx stubs if they're incomplete; check after first run).

## Rollback / cleanup

```bash
# Stop and remove Metabase (keep PG running)
uv run python -m src.cli.main metabase teardown --remove-volume

# Or, equivalently, all the way down:
uv run python -m src.cli.main teardown --remove-volume  # also stops PG
```

After teardown with `--remove-volume`, the next `metabase setup` is a fresh image (no admin user, no cards). The PG warehouse persists independently of Metabase.
