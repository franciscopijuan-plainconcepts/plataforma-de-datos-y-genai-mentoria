# Implementation Plan: Data and GenAI Platform – Baseline

**Branch**: `001-data-genai-platform-baseline` | **Date**: 2026-07-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-data-genai-platform-baseline/spec.md`
**Related**: [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md)

**Note**: This template is filled in by the `/speckit.plan` command; its definition describes the execution workflow.

## Summary

The baseline (v0) delivers a locally-running, containerized PostgreSQL data warehouse containing the three relational Global Superstore tables (`Orders`, `Returns`, `People`) plus a comprehensive data dictionary document. The technical approach is **EDA-driven schema design**: rather than hardcoding PostgreSQL data types upfront, a Python data-exploration script inspects `Global Superstore Data.xlsx` (formats, ranges, nullability, categorical vs free-form, primary-key candidates, inter-table links) and **derives** the optimal schema, which is then materialized into PostgreSQL via an engine-agnostic, typed data-access layer conforming to the constitution. The data dictionary integrates the Kaggle semantic descriptions (`Orders`: Transactional Logs; `Returns`: Reverse Logistics; `People`: Sales Governance) with the EDA-discovered types into a single human-readable artifact. All ingestion, schema, dictionary, and validation components are organized into strictly separated Data Engineering modules with typed cross-boundary contracts, leaving AI Engineering (Text-to-SQL) and MLOps for later milestones.

## Technical Context

**Language/Version**: Python 3.11+ (strict typing enforced — Pyright/Pylance strict or `mypy --strict`).

**Primary Dependencies**:
- Data access / warehousing: **`psycopg` (v3), synchronous API** (resolved in [research.md](./research.md)) as the PostgreSQL driver, confined to `src/data_access/adapters/postgres/`. Async rejected (no throughput benefit for a local CLI). `psycopg2` rejected (legacy). See research.md Part B for full rationale.
- Data exploration / ingestion: `pandas` (EDA + Excel read), `openpyxl` (Excel engine), SQL DDL execution via the data-access layer.
- Data contracts: `pydantic` v2 (typed models at layer boundaries) or frozen `dataclasses`.
- Type checking: Pyright/Pylance strict (or `mypy --strict`); `pytest` for tests.
- Infrastructure: Docker + Docker Compose for the local PostgreSQL instance.
- Build/packaging: `uv` or `poetry` with a reproducible lockfile.

**Storage**: PostgreSQL 15+ running locally in Docker as the only local data store; engine-specific code confined to the data-access adapter.

**Testing**: `pytest` with contract tests (typed-boundary contracts), integration tests against the Dockerized PostgreSQL, and unit tests for the EDA/schema-inference logic.

**Target Platform**: Linux/macOS/Windows local developer machine running Docker. No cloud or production deployment in this baseline.

**Project Type**: Library + CLI tooling (data engineering pipelines, EDA, ingestion, dictionary generation, validation). Not a web service / mobile app / compiler.

**Performance Goals**: Not latency-sensitive. EDA over the Global Superstore Excel (~10k order rows) completes in seconds; full reload of the warehouse completes in under a minute.

**Constraints**: Offline-capable (no network required after dependencies are installed); single-machine local dev; no external services.

**Scale/Scope**: One Excel source file with three logical tables; ~10k rows in `Orders`; small `Returns` and `People` tables. No multi-tenant scale concerns at this stage.

**Open clarifications (deferred to Phase 0 research)**:
- Exact PostgreSQL driver and connection approach — sync vs async, and which library best keeps the data-access layer engine-agnostic for the future BigQuery migration.
- Final PostgreSQL data types per column — intentionally **NOT** hardcoded here; derived by the EDA step in Phase 0/implementation. (See Assumptions in spec.)
- Minimal MLOps/versioning footprint required at baseline to satisfy "Reproducible MLOps" without prematurely building model-training infrastructure.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle (Constitution v1.0.0) | Status | Baseline plan compliance |
|---|---|---|
| I. Strictly-Typed Python Foundation | ✅ Pass | Python 3.11+; type hints on every signature; `mypy --strict`/Pyright strict on CI; `Any` only with inline justification; Pydantic v2 / frozen dataclasses at every boundary. |
| II. Layered Separation of Concerns (NON-NEGOTIABLE) | ✅ Pass | Only the **Data Engineering** domain is implemented in this baseline (EDA, ingestion, dictionary, validation). AI Engineering (Text-to-SQL) and MLOps are out of scope. Cross-domain traffic flows only through typed contracts in `contracts/`; no upward or sideways imports. |
| III. Portable Data Access & Abstraction | ✅ Pass | Local dev is Docker-only with PostgreSQL as the only local data store. All data access flows through an abstracted, typed data-access layer; engine-specific code (PostgreSQL driver, DDL) confined to adapter implementations to allow seamless future BigQuery migration with no upstream changes. |
| IV. Data Governance by Default (NON-NEGOTIABLE) | ⚠️ Partial / Deferred | RBAC/RLS at the Semantic Layer is explicitly **v2.0 scope** per the spec. The baseline MUST NOT introduce governance logic prematurely, but the data model MUST preserve the columns needed for future RLS on `People` (region) and business logic on `Returns`. No feature without a Semantic Layer may claim to provide governance. |
| V. Reproducible MLOps | ✅ Pass (minimal) | Baseline does not train models. Reproducibility is enforced via lockfile-based deterministic dependencies, deterministic Dockerized PostgreSQL, and EDA-derived schema that is regenerated from the source. Experiment tracking is N/A for this milestone (no models); MLOps infrastructure is deferred. |

**Gate status**: PASS. No violations require justification. Principle IV is intentionally deferred to v2.0 per the spec's roadmap, with the data model kept governance-ready — this is a scoping decision, not a violation.

### Post-design Re-check (Phase 1)

Re-verified after generating `research.md`, `data-model.md`, `contracts/`, and `quickstart.md`:

| Principle | Post-design status | Evidence |
|---|---|---|
| I. Strictly-Typed Python | ✅ Pass | All contract models (`ColumnDef`, `TableDef`, `OrderRow`, `ReturnRow`, `PersonRow`, `SchemaInferenceResult`, `LoadResult`, `DataDictionaryDocument`) are Pydantic v2 models with explicit types. Money columns typed as `Decimal` (NOT `float`). Protocols typed to return/accept contract models — never `Any`/`dict`. `tests/contract/` enforces conformance. |
| II. Layered Separation of Concerns | ✅ Pass | Only Data Engineering domain implemented. `contracts/` holds the shared downward-dependency layer; `data_engineering/` imports only contracts + data-access Protocols. Boundary enforcement in `contracts/ingestion.md` asserts: no `pandas` outside `data_engineering/eda\|ingestion`; no `psycopg` outside `data_access/adapters/postgres/`. |
| III. Portable Data Access & Abstraction | ✅ Pass | `data-model.md` uses engine-neutral `LogicalType`; PG types rendered only in the adapter. `data_access.md` Protocol methods are semantic (no raw `execute_sql`). `find_orders_by_region` and `load_rows` designed bulk-load-semantic so the future BQ adapter maps to a load job, not `INSERT`-per-row. BigQuery adapter directory exists as a stub. |
| IV. Data Governance by Default | ✅ Pass (deferred) | `data-model.md` § Governance-Readiness confirms RLS anchors preserved: `Orders.Region`/`Returns.Region` first-class columns; `People.Person` PK normalized at load; `Returns.Order ID ↔ Orders.Order ID` preserved for net-vs-gross. No RBAC/RLS logic implemented (correctly deferred to v2.0 per spec). |
| V. Reproducible MLOps | ✅ Pass (minimal) | `LoadArtifactManifest` (provenance: source sha256, schema version, row counts, git commit, tool versions) wired into the ingestion contract and consumed by the validator (FR-014). `uv` lockfile + deterministic Dockerized PG + git tags cover reproducibility. MLflow/DVC deferred until first model (v1.x+). |

**Post-design gate status**: PASS. No violations. Principle IV remains a documented, justified deferral (v2.0 scope) with the data model kept governance-ready — not a violation.

## Project Structure

### Documentation (this feature)

```text
specs/001-data-genai-platform-baseline/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (EDA findings + technical decisions)
├── data-model.md        # Phase 1 output (entities, fields, derived types, relationships)
├── quickstart.md        # Phase 1 output (runnable validation guide)
├── contracts/           # Phase 1 output (typed cross-boundary interfaces)
│   ├── data_access.md   # Data-access layer interface (Repository/Provider contracts)
│   ├── ingestion.md     # EDA -> schema -> ingestion pipeline contracts
│   └── dictionary.md    # Data-dictionary generation contracts
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
docker/
└── docker-compose.yml        # PostgreSQL service definition

src/
├── contracts/                # Shared typed contracts (Pydantic v2 / frozen dataclasses)
│   ├── __init__.py
│   ├── data_access.py        # Data-access interfaces (Protocols) + dataset/table/column models
│   ├── ingestion.py          # EDA report, schema-inference result, load-result contracts
│   └── dictionary.py         # Data-dictionary document model contracts
├── data_engineering/         # Data Engineering domain (the only domain in this baseline)
│   ├── __init__.py
│   ├── eda/                  # Exploratory Data Analysis (Excel inspection -> schema inference)
│   │   ├── __init__.py
│   │   ├── explorer.py       # Reads .xlsx, infers types/keys/relationships
│   │   └── schema_inferrer.py # Produces typed SchemaInferenceResult
│   ├── ingestion/            # Schema materialization + data load (via data-access layer)
│   │   ├── __init__.py
│   │   ├── schema_builder.py # Builds DDL from inferred schema (engine-specific, in adapter)
│   │   └── loader.py         # Loads rows from Excel -> data-access layer -> PostgreSQL
│   ├── dictionary/           # Data-dictionary generation
│   │   ├── __init__.py
│   │   ├── semantic_source.py # Kaggle descriptions (Orders/Returns/People semantics)
│   │   └── generator.py      # Merges EDA types + Kaggle semantics -> dictionary doc
│   └── validation/           # Health-check + validation commands
│       ├── __init__.py
│       └── validator.py      # Container up, DB reachable, 3 tables, non-empty, dict present
├── data_access/             # Abstracted data-access layer (Repository/Provider pattern)
│   ├── __init__.py
│   ├── interfaces.py        # Typed Protocols: DataProvider, SchemaProvider, QueryProvider
│   └── adapters/
│       ├── __init__.py
│       ├── postgres/        # PostgreSQL adapter (DDL, reads, writes; engine-specific)
│       │   ├── __init__.py
│       │   ├── connection.py
│       │   └── repository.py
│       └── bigquery/        # Future adapter stub (not implemented in this baseline)
│           └── __init__.py
└── cli/                     # CLI entrypoints (bootstrap, teardown, validate, generate-dictionary)
    ├── __init__.py
    └── main.py

tests/
├── contract/                # Typed-boundary contract tests (data_access, ingestion, dictionary)
├── integration/             # Dockerized-PostgreSQL integration tests (no mocked data stores)
└── unit/                    # Unit tests for EDA / schema inference / dictionary generation

.env.example                 # Documented connection parameters (gitignored in .env)
Global Superstore Data.xlsx  # Source dataset (already present in repo root)
pyproject.toml               # Python project + tooling config (uv/poetry, mypy/pyright, pytest)
```

**Structure Decision**: Single-project (Option 1) layout because the baseline is a single engineering domain (Data Engineering) producing library code + CLI tooling, with no frontend/backend or mobile split. The repository is organized so the `src/contracts/` package is the dependencies-downward shared layer (no domain imports it up), `src/data_engineering/` is the only implemented domain, and `src/data_access/` holds the abstracted, typed data-access layer with engine-specific code confined to `src/data_access/adapters/<engine>/`. This satisfies the constitution's Principle II (cross-domain traffic only via typed contracts in `contracts/`) and Principle III (engine-specific code confined to adapters). The `src/data_access/adapters/bigquery/` directory is kept as a placeholder stub to make the future migration path concrete.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. Table intentionally left empty — all constitutional principles are satisfied or are intentionally deferred per the spec's roadmap (Principle IV / Governance → v2.0).
