# Implementation Plan: Semantic Layer v1 (Governed Metrics, Dimensions & RLS)

**Branch**: `003-semantic-layer-v1` | **Date**: 2026-08-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-semantic-layer-v1/spec.md`
**Related**: [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md)

## Summary

El milestone **v2.0** entrega la **Semantic Layer** declarativa y gobernada que faltaba: un `SemanticLayerDocument` (Pydantic v2, artifact regenerable) con métricas, dimensiones y relaciones derivadas de las fuentes existentes (`semantic_source.py`, `DataDictionaryDocument`); un `SemanticQueryResolver` que aplica RLS por `Region` usando `People` como mapping `viewer → regions`; y la integración con el pipeline de Text-to-SQL de la feature `002` para que ningún SQL del LLM bypassa la gobernanza (constitution Principle IV, NON-NEGOTIABLE).

El approach técnico es deliberadamente minimalista y **reutiliza** el contexto que ya existe en vez de inventar de cero:

- Las métricas (`net_sales`, `return_rate`, etc.) se derivan del `semantic_source.py` y se documentan en el `SemanticLayerDocument` — no se construye un DSL runtime de métricas.
- El RLS usa un patrón de **subquery wrapping** que es robusto sin depender del parseo exacto del LLM, consistente con el enfoque YAGNI del `SqlValidator` de 002. No se introduce un parser SQL completo (`sqlglot`).
- El `PromptBuilder` se extiende con un bloque opcional de métricas/dimensiones; el fallback al comportamiento de 002 (solo `DataDictionaryDocument`) se mantiene para no romper tests existentes.

## Technical Context

**Language/Version**: Python 3.11+ (estricto — `mypy --strict` ya configurado en `pyproject.toml`; pinned a 3.13 via `.python-version`). Sin cambios respecto de 001/002.

**Primary Dependencies** (sin adicionar nuevas pesadas):
- **`pydantic` v2 (>=2.7)** — para todos los contract models nuevos en `src/contracts/semantic_layer.py`.
- **`psycopg` (existente)** — confined a `src/data_access/adapters/postgres/`. El resolver no usa `psycopg`.
- **`python-dotenv` (existente)** — para `SEMANTIC_VIEWERS_*` env vars.
- **`pyyaml` (`pyyaml>=6.0`, NUEVA, liviana)** — para el archivo `viewers.yaml`. Justificación: una sola dependencia liviana, ampliamente usada, alineada con YAGNI (no creamos un parser custom). Se agrega a `pyproject.toml`.

**Storage**: PostgreSQL 15 en Docker (sin cambios). Tablas `Orders`, `Returns`, `People` ya cargadas. **No se crean tablas nuevas** — el Semantic Layer es una capa lógica/declarativa sobre las tablas existentes; el "rewriting" de SQL ocurre en runtime en el resolver, no como vistas SQL. (Decisión explícita en `research.md` Part A: crear vistas rompería la idempotencia del `bootstrap` de la baseline; preferimos rewriting en runtime, consistente con el enfoque del `SqlValidator`.)

**Testing**: `pytest` (existente). Se extienden:
- `tests/contract/test_boundaries.py` — assert RLS solo se invoca desde el camino del `QueryProvider`; `openai`/`httpx` still confined; plus semantic-layer contract tests.
- `tests/contract/test_semantic_layer.py` (NUEVO) — contract tests para todos los Pydantic models + el resolver (puro, sin DB) + el builder invariants.
- `tests/unit/test_semantic_resolver.py` (NUEVO) — unit tests del `apply_rls` (covers: viewer con una región, viewer con varias, viewer vacío, viewer con `allows_full_access`, SQL con WHERE existente, SQL sin WHERE, SQL con JOIN, SQL con GROUP BY).
- `tests/integration/test_semantic_rls.py` (NUEVO) — integration test contra PostgreSQL real en Docker con dos viewers de regiones distintas; verifica que los resultados coinciden con `SELECT ... WHERE Region IN (...)` directo.
- `tests/integration/test_text_to_sql.py` (existente, extendido) — ahora corre `ask` con un viewer activo y verifica que el SQL ejecutado incluye el filtro de región.

**Target Platform**: Linux/macOS/Windows local developer machine con Docker. Sin nube, sin servidor web.

**Project Type**: Library + CLI tooling (extiende la baseline + AI Engineering con un subpaquete nuevo de Semantic Layer bajo Data Engineering, y modifica `ai_engineering/prompt_builder.py` y `pipeline.py`).

**Performance Goals**: El rewriting de RLS añade <5ms por query (string composition, no DB round-trip). `generate-semantic-layer` corre en <5 segundos (no requiere DB viva). `ask --viewer` añade 0ms percibido sobre `ask` de 002 (el overhead está dentro del noise del LLM round-trip).

**Constraints**:
- RLS NO puede ser bypassado por ningún path de código (constitution Principle IV, NON-NEGOTIABLE). Boundary test lo fuerza.
- `semantic_layer.json` es determinista (sin timestamps en el JSON).
- `ask` sin viewer falla rápido salvo `--allow-full-access` y solo en `ENV in {local, dev, test}`.
- Mismatch `People.Region` (Eastern/Western Canada) vs `Orders.Region` (Canada) NO se resuelve aquí (v3.0+); el matching es best-effort y conservador.

**Scale/Scope**: 3 tablas (Orders 51k, Returns 2k, People 24 rows). 8 métricas. ~11 dimensiones. 2 relaciones. Viewers declarados en archivo local. Single-tenant.

**Open clarifications (resueltas en Phase 0 investigación)** → ver `research.md`:

1. **Cómo aplicar RLS al SQL** → Decisión: **subquery wrapping** (research.md Part A). Robusto sin parsear SQL.
2. **Formato del archivo de viewers** → Decisión: **YAML** (`viewers.yaml` vía `pyyaml`). Justificado en research.md Part C.
3. **Integración del `SemanticLayerDocument` en el `PromptBuilder`** → Decisión: **condensación selectiva** (~+300-500 tokens, solo métricas/dimensiones/joins). Ver research.md Part B.
4. **Determinismo de `semantic_layer.json`** → Decisión: **canonical JSON sin `generated_at`** (timestamp solo va al `.md`); serializa con `sort_keys=True` + `exclude_none`. Ver research.md Part D.
5. **Boundary enforcement** → Decisión: **`GovernedQueryProvider` decorator** + AST/grep boundary test + integration test. Ver research.md Part E + `contracts/integration.md`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle (Constitution v1.0.0) | Status | v2.0 plan compliance |
|---|---|---|
| I. Strictly-Typed Python Foundation | PASS | Python 3.11+; `mypy --strict` ya configurado. Todos los contract models nuevos (`SemanticLayerDocument`, `Metric`, `Dimension`, `SemanticViewer`, `SemanticRelationship`, `SemanticQueryResolverProtocol`) son Pydantic v2 frozen con tipos explícitos. El `apply_rls` es fuertemente tipeado: `(sql: str, viewer: SemanticViewer, table_def: TableDef) -> str`. Sin `Any` nuevo (salvo donde la constitución lo permite con justificación inline, heredado de 002). |
| II. Layered Separation of Concerns (NON-NEGOTIABLE) | PASS | Un subpaquete nuevo `src/data_engineering/semantic_layer/` aloja builder + resolver + registry + metrics + render (la semántica es de Data Engineering per constitution). `src/ai_engineering/` se modifica pero solo consume el `SemanticLayerDocument` via `src/contracts/semantic_layer.py` (typed contract) — NO importa `data_engineering.semantic_layer` directamente (quebraría Principle II). La inyección del `GovernedQueryProvider` en el `QueryProvider` se hace vía constructor composition en `cli/main.py` (composition root), no por import cross-domain interno. |
| III. Portable Data Access & Abstraction | PASS | El `SemanticQueryResolver` es engine-neutral (opera sobre el SQL string, sin `psycopg`/`bigquery`). El PG adapter existente solo recibe el SQL ya gobernado y lo ejecuta: no conoce nada de RLS. La futura migración a BigQuery solo necesita un `BigQueryRepository` que implemente `execute_readonly_query`; el resolver vive antes del adapter y es agnóstico al engine (recibe SQL string, devuelve SQL string). |
| IV. Data Governance by Default (NON-NEGOTIABLE) | **PASS (esta feature lo satisface por primera vez)** | **Principle IV pasa de DEFERRED a PASS**: el `SemanticQueryResolver.apply_rls` fuerza `Region IN (viewer.regions)` en TODO SQL que pasa por el `QueryProvider`. El boundary test garantiza que ningún caller de `execute_readonly_query` evita el resolver. RLS por fila está enforced; RBAC column-level queda declarado pero no enforced en v2.0 (mejora incremental v3.0+). Audit logging básico del viewer y SQL gobernado se agrega al `.artifacts/text_to_sql.log` existente. |
| V. Reproducible MLOps | PASS (mejorado) | El `SemanticLayerDocument` es versionado (artifact en `.artifacts/`) y determinista (json canonical). El `SemanticViewer` activo se loguea con cada call (extiende el logging de 002 FR-014 con gobernanza). El `SemanticLayerDocument` se referencia por su `source_sha256` (link al `load_manifest.json`). |

**Gate status**: PASS. Principle IV, antes DEFERRED en 001 y 002, se satisface por primera vez.

## Project Structure

### Documentation (this feature)

```text
specs/003-semantic-layer-v1/
├── plan.md              # This file (/speckit.plan output)
├── research.md          # Phase 0 output (RLS strategy, prompt integration, viewer config, determinism, boundary enforcement)
├── data-model.md        # Phase 1 output (Semantic Layer contract models)
├── quickstart.md        # Phase 1 output (runnable validation guide)
├── contracts/           # Phase 1 output (typed cross-boundary interfaces)
│   ├── semantic_layer.md    # Semantic Layer document + viewer + resolver contracts
│   └── integration.md       # Integration contract with Text-to-SQL (how RLS intercepts QueryProvider)
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── contracts/                          # Shared typed contracts
│   ├── data_access.py                  # (existing) TableDef, ColumnDef, Row...
│   ├── ingestion.py                    # (existing)
│   ├── dictionary.py                   # (existing) DataDictionaryDocument
│   ├── text_to_sql.py                  # (existing) Text-to-SQL contract models (unchanged)
│   └── semantic_layer.py               # (NEW) SemanticLayerDocument, Metric, Dimension,
│                                       #       SemanticRelationship, SemanticViewer,
│                                       #       SemanticQueryResolverProtocol
├── data_access/                        # (existing) Engine-agnostic data-access layer
│   ├── interfaces.py                   # (UNCHANGED) — QueryProvider Protocol keeps its
│   │                                   #   method signature; the resolver is composed in,
│   │                                   #   not added to the Protocol
│   └── adapters/postgres/
│       ├── connection.py               # (existing, unchanged)
│       └── repository.py                # (UNCHANGED) — still executes the SQL it receives;
│                                       #   the resolver runs BEFORE this layer in the pipeline
│                                       #   (separation: governance in Semantic Layer, execution
│                                       #   in adapter)
├── data_engineering/                   # (existing) Data Engineering domain — gets a new subpackage
│   ├── dictionary/                     # (existing)
│   ├── eda/                            # (existing)
│   ├── ingestion/                      # (existing)
│   ├── validation/                     # (existing)
│   └── semantic_layer/                 # (NEW) Semantic Layer implementation
│       ├── __init__.py
│       ├── builder.py                   # Builds SemanticLayerDocument from semantic_source +
│       │                                #   DataDictionaryDocument (no DB needed)
│       ├── resolver.py                  # SemanticQueryResolver — pure function apply_rls
│       ├── governed_provider.py         # GovernedQueryProvider decorator that wraps a
│       │                                #   QueryProvider and enforces RLS on every call
│       ├── registry.py                  # Loads viewers from viewers.yaml (+ env override)
│       ├── metrics.py                   # Defined metrics (gross_sales, net_sales, return_rate, ...)
│       └── render.py                    # Serializes SemanticLayerDocument to .md and .json
├── ai_engineering/                     # (existing) — small modifications to integrate
│   ├── llm_client.py                   # (existing, unchanged)
│   ├── prompt_builder.py                # (MODIFIED) accepts optional SemanticLayerDocument,
│   │                                   #   adds condensed metrics/dimensions block (~+400 tokens)
│   ├── sql_validator.py                 # (existing, unchanged)
│   ├── pipeline.py                     # (MODIFIED) accepts optional SemanticViewer; routes the
│   │                                   #   validated SQL through the resolver before execution
│   └── evaluation.py                   # (existing, unchanged in this feature)
└── cli/
    └── main.py                          # (MODIFIED) adds `generate-semantic-layer` command;
                                        #   extends `ask` with `--viewer <id>` (and optional
                                        #   `--allow-full-access` for local/dev); composes the
                                        #   GovernedQueryProvider into the QueryProvider

# New artifact directory entries
.artifacts/
├── load_manifest.json                  # (existing)
├── text_to_sql.log                     # (existing, extended fields: viewer_id, regions, gov_bypass)
├── semantic_layer.json                 # (NEW) canonical, deterministic JSON artifact
└── semantic_layer.md                   # (NEW) human-readable artifact

# Viewer config (gitignored, local-only)
viewers.example.yaml                    # (NEW, committed) example/template
viewers.yaml                            # (gitignored, local-only; loader reads this by default
                                        #   or whatever SEMANTIC_VIEWERS_FILE points to)

tests/
├── contract/
│   ├── test_boundaries.py              # (MODIFIED) extends existing asserts with: (a) RLS
│   │                                   #   resolver import only from data_access path; (b)
│   │                                   #   SemanticLayer contracts are Pydantic v2; (c)
│   │                                   #   no new openai/psycopg leakage; (d) AST/grep check
│   │                                   #   that no caller of execute_readonly_query bypasses
│   │                                   #   the GovernedQueryProvider wrapper
│   ├── test_text_to_sql.py             # (existing, unchanged — pipeline still satisfies its
│   │                                   #   own contract)
│   ├── test_dictionary.py              # (existing, unchanged)
│   ├── test_data_access.py             # (existing, unchanged)
│   └── test_semantic_layer.py          # (NEW) contract tests for Semantic Layer models +
│                                       #   builder invariants + resolver Protocol conformance
├── integration/
│   ├── test_reproducibility.py         # (existing, unchanged)
│   ├── test_warehouse.py               # (existing, unchanged)
│   ├── test_text_to_sql.py             # (existing, EXTENDED — adds a scenario that runs `ask`
│   │                                   #   with --viewer and verifies the SQL has Region filter)
│   └── test_semantic_rls.py            # (NEW) two-viewer integration test against Dockerized PG
│                                       #   — verifies RLS actually filters rows
└── unit/
    ├── test_sql_validator.py           # (existing, unchanged)
    └── test_semantic_resolver.py       # (NEW) pure unit tests for apply_rls — no DB, no LLM

# Documentation updates (root readmes — user-requested scope add)
README.md                               # (MODIFIED) bump status to v2.0, add Semantic Layer section,
                                        #   add `generate-semantic-layer` + `ask --viewer` to
                                        #   quickstart, refresh Roadmap to v3.0 next
README_STATUS.md                        # (MODIFIED) M3 milestone marked completed; add v2.0 scope
                                        #   (Semantic Layer + RLS); refresh roadmap (v3.0 next);
                                        #   refresh "Estado actual" snapshot date + branch
README_SPECKIT.md                       # (MODIFIED) add feature 003 to "lo que ya hicimos" list;
                                        #   add semantic_layer contract reference; add
                                        #   viewers.yaml pattern to "convenciones acordadas"
```

**Structure Decision**: Single-project layout (extendiendo Option 1 de 001/002). El subpaquete nuevo `src/data_engineering/semantic_layer/` aloja la implementación (builder, resolver, governed_provider, registry, metrics, render). Sus contract models viven en `src/contracts/semantic_layer.py` (typed boundary — Principle I/II). El `ai_engineering` se modifica mínimamente para integrar `SemanticLayerDocument` en el `PromptBuilder` y `SemanticViewer` + `SemanticQueryResolver` en el `pipeline`. La inyección del resolver se hace en `cli/main.py` (composition root), NO via import cross-domain interno. El `PostgresRepository` NO se modifica — recibe SQL ya gobernado y lo ejecuta; la separación governance/execution es explícita y compatible con migración futura a BigQuery (el `BigQueryRepository` futura solo implementará `execute_readonly_query` de la misma forma).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. Principle IV pasa de DEFERRED a PASS — es justamente el objetivo de esta feature. Table intentionally left empty.
