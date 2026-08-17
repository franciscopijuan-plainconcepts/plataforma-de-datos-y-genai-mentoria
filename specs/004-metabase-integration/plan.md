# Implementation Plan: Metabase Integration (Governed SQL Cards from Chat Sessions)

**Branch**: `004-metabase-integration` | **Date**: 2026-08-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-metabase-integration/spec.md`
**Related**: [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md)

## Summary

El milestone **v2.1** entrega una integración con **Metabase** que permite visualizar como cards/dashboards las consultas SQL gobernadas generadas por el pipeline de Text-to-SQL de la feature `002`. Metabase se levanta en Docker junto a PostgreSQL, se setupeeo automáticamente vía REST API (`metabase setup`), y al final de cada `ask` exitoso el SQL **ya gobernado** (post-`GovernedQueryProvider`, con `WHERE "Region" IN (viewer.regions)` inyectado) se envía a Metabase como una card. La governance NON-NEGOTIABLE de la feature `003` se preserva: el SQL que llega a Metabase ya tiene RLS aplicada, así que aunque Metabase lo re-ejecute, los resultados siguen scopeados por el viewer original.

El approach técnico es deliberadamente minimalista y reutiliza lo existente:

- **Reutilización del `GovernedQueryProvider`**: la integración NO toca el Semantic Layer ni el resolver. Solo consume el SQL gobernado que el pipeline ya genera.
- **Metabase como capa de visualización opcional (best-effort)**: si Metabase no está disponible, el pipeline sigue funcionando. La integración se inyecta como callback opcional en el `TextToSqlPipeline`.
- **Setup reproducible**: igual que el `bootstrap` para PG, un solo comando `metabase setup` hace todo el bootstrap automatizado via API.
- **Defense-in-depth en governance**: además del SQL gobernado que llega a las cards, el role de PostgreSQL que Metabase usa para conectar es read-only (no puede escribir).

## Technical Context

**Language/Version**: Python 3.11+ (estricto — `mypy --strict` ya configurado). Sin cambios respecto a 001/002/003.

**Primary Dependencies**:
- **`httpx` (ya transitivo vía `openai`)** — para HTTP calls a la API de Metabase. NO usamos `requests` porque `httpx` ya está en el env (tomamos ventaja de evitar una nueva dependencia). Justificado en research.md Part A.
- **`pydantic` v2** — para los contract models nuevos en `src/contracts/metabase.py`.
- **`psycopg` (existente)** — confined a `src/data_access/adapters/postgres/`. El setup del role metabase_readonly usa el adapter existente.
- **Docker Compose (existente)** — para levantar el container de Metabase junto a PG.
- **NO se añade `requests`** — explicitly reusamos `httpx` que ya está en el ambiente via `openai`.

**Storage**: PostgreSQL 15 + Metabase H2 (en container, volcado a un volumen Docker para persistencia). No se introduce ningún otro storage.

**Testing**: `pytest` (existente). Se extienden:
- `tests/contract/test_boundaries.py` — assert `httpx` imports sólo en `src/ai_engineering/metabase_client.py` (boundary nuevo); no leakage.
- `tests/contract/test_metabase.py` (NUEVO) — contract tests para los modelos Pydantic + el cliente.
- `tests/unit/test_metabase_client.py` (NUEVO) — unit tests del cliente usando una fake HTTP layer (no requiere Metabase corriendo).
- `tests/integration/test_metabase_integration.py` (NUEVO) — integration test contra Metabase corriendo en Docker + Forge; valida end-to-end que un `ask --viewer <id>` crea una card cuya re-ejecución desde la API de Metabase devuelve sólo las regiones del viewer (SC-003 NON-NEGOTIABLE check).

**Target Platform**: Linux/macOS/Windows local developer machine con Docker.

**Project Type**: Library + CLI tooling (extiende el CLI con `metabase setup|status|teardown|reset-cards` y modifica `ask` con `--send-to-metabase`/`--no-metabase` y `--session`).

**Performance Goals**: El HTTP request a Metabase (creación de una card) debe tomar <2s en local (la card crítica path para el UX es verla luego en la UI). El `ask` no debe bloquearse más de 5s esperando a Metabase — si tarda más, se loguea warning y se continúa (best-effort, FR-013).

**Constraints**:
- Metabase sólo puede ejecutar SQL ya gobernado (Principle IV preserved por diseño).
- El role `metabase_readonly` en PostgreSQL sólo tiene grants SELECT (defense-in-depth).
- La integración con Metabase es best-effort (nunca bloquea el pipeline si Metabase falla).
- El `MetabaseClient` es la única clase que hace HTTP a Metabase (boundary test lo enforce).
- El token de sesión de Metabase nunca se loguea (FR-008).

**Scale/Scope**: Una instancia de Metabase local, un admin user, una colección "Chat Sessions". Cards creadas incrementalmente por cada `ask` exitoso; sessions opcionales.

**Open clarifications (resueltas en Phase 0 investigación)** → ver `research.md`:

1. **Cliente HTTP: `requests` vs `httpx`** → Decisión: `httpx` (ya transitivo vía `openai`). Research Part A.
2. **Versión de la imagen de Metabase a usar y estrategia de pinning** → Decisión: tag `v0.48-latest` pinned por major+minor (rechazar breaking changes). Research Part B.
3. **Cómo inicializar el role `metabase_readonly` en PostgreSQL** → Decisión: en `metabase setup`, vía DDL `CREATE ROLE`/`GRANT SELECT` ejecutado con el adapter existente. Research Part C.
4. **Cómo injectar `MetabaseClient` en `TextToSqlPipeline` sin acoplamiento** → Decisión: callback opcional `on_query_complete` (un `Callable[[QueryResult, ...], None]`) en el constructor; el CLI encola el envío a Metabase. Research Part D.
5. **Display type (chart type) de las cards creadas automáticamente** → Decisión: heurística simple — `scalar` para single-value totals (aggregations), `table` para GROUP BY, `bar` si la.columna GROUP BY es una dimension categorical. Research Part E.
6. **Metabase API: idempotencia del setup** → Decisión: el `metabase setup` chequea `GET /api/session/properties` para ver si el setup ya fue hecho (devuelve `setup-token: null`), y saltea ese paso si así. Research Part F.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle (Constitution v1.0.0) | Status | v2.1 plan compliance |
|---|---|---|
| I. Strictly-Typed Python Foundation | PASS | Python 3.11+, `mypy --strict` ya configurado. Todos los contract models nuevos (`MetabaseConfig`, `Card`, `Collection`, `Dashboard`, `MetabaseClient`) son Pydantic v2 frozen o dataclasses con tipos explícitos. El `MetabaseClient` es fuertemente tipado: `login() -> str`, `create_card(name: str, sql: str, collection_id: int, display: str) -> Card`. Sin `Any` nuevo (salvo la respuesta JSON de la API de Metabase que se parsea a un dict[str, object], justified inline). |
| II. Layered Separation of Concerns (NON-NEGOTIABLE) | PASS | Un módulo nuevo `src/ai_engineering/metabase_client.py` aloja en cliente HTTP — es parte del dominio AI Engineering porque es un post-step del pipeline del LLM (recibe el SQL gobernado y lo envía a un sink de visualización). NO se importa en `data_engineering` ni `data_access` (es el final de la cadena, no fluye hacia atrás). Cross-domain communication via callback `on_query_complete` que el CLI inyecta. Boundary test extiende: `httpx` importado solo en `metabase_client.py`. |
| III. Portable Data Access & Abstraction | PASS | `MetabaseClient` sólo hace HTTP → Metabase API; no conoce PostgreSQL ni adapters. El role `metabase_readonly` se crea via el adapter existente (`psycopg` confined a `data_access/adapters/postgres/`). Si migras a BigQuery en el futuro, el `MetabaseClient` no requiere cambios (Metabase se conectaría a BigQuery con su propia configuración). |
| IV. Data Governance by Default (NON-NEGOTIABLE) | PASS (preserved by design) | **Principle IV SIGUE PASS** (lo conseguimos en `003`). Metabase NO bypassa governance: las cards contienen SQL `ya gobernado` (`WHERE "Region" IN` ya inyectado por el `SemanticQueryResolver`). + Defense-in-depth: el role `metabase_readonly` en PG sólo permite SELECT. La única "falla" sería si alguien escribe SQL ad-hoc via Metabase editor — pero eso está OUT OF SCOPE (la API de escritura no se expone) + read-only role lo limita. Documentado en SC-003. |
| V. Reproducible MLOps | PASS (mejorado) | El setup de Metabase es reproducible via `metabase setup` desde clean clone. Logging extiende el `.artifacts/text_to_sql.log` con `metabase_card_id` y status (success/skipped/error) para cada `ask` exitoso. El artifact de Metabase (cards + colección) NO es parte determinística del repo (Metabase tiene su propio state en H2/volumen) — el partiality se documenta. |

**Gate status**: PASS. Principle IV se PRRSERVA (no se weaken) porque Metabase se integra DESPUÉS del `GovernedQueryProvider` y el SQL que llega a las cards ya está gobernado; además, otro layer de defense-in-depth con role read-only.

## Project Structure

### Documentation (this feature)

```text
specs/004-metabase-integration/
├── plan.md              # This file
├── research.md          # Phase 0 output (A-F decisions)
├── data-model.md        # Phase 1 output (Metabase contracts)
├── quickstart.md        # Phase 1 output (runnable validation guide)
├── contracts/           # Phase 1 output (boundaries)
│   ├── metabase_client.md   # MetabaseClient interface (HTTP boundary)
│   └── pipeline_integration.md # How TextToSqlPipeline calls MetabaseClient
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
docker/
└── docker-compose.yml   # (MODIFIED) adds the `metabase` service alongside `postgres`

src/
├── contracts/
│   ├── data_access.py   # (existing, unchanged)
│   ├── semantic_layer.py # (existing, unchanged)
│   └── metabase.py      # (NEW) MetabaseConfig, Card, Collection, Dashboard, MetabaseSession
├── data_access/
│   └── adapters/postgres/
│       └── repository.py # (UNCHANGED) — the readonly role is created via a new helper, not in repo
├── ai_engineering/
│   ├── metabase_client.py # (NEW) MetabaseClient — the ONLY module that imports httpx
│   ├── llm_client.py    # (existing, unchanged)
│   └── prompt_builder.py # (existing, unchanged)
├── data_engineering/
│   └── semantic_layer/   # (existing, unchanged)
│   └── (other existing unchanged)
├── cli/
│   └── main.py          # (MODIFIED) commands: metabase setup|status|teardown|reset-cards;
│                         # ask extended with --no-metabase + --session <id> flags;
│                         # composition: builds MetabaseClient and wires on_query_complete callback.
└── data_access/
    └── adapters/
        └── postgres/
            └── roles.py  # (NEW) `ensure_metabase_readonly_role(repo) -> None` — creates the
                          # read-only PG role via the existing adapter. Confined to PG adapter.

# New artifact directory
.artifacts/
├── load_manifest.json  # (existing)
├── text_to_sql.log     # (existing, extended with metabase_card_id / metabase_status)
└── metabase_state.json # (NEW) tracks last-known metabase config: admin_user, db_id, collection_id
                          # written by `metabase setup` so subsequent commands can reuse it.

# Metabase config (gitignored)
.env                    # (MODIFIED) adds METABASE_* vars (admin email, pass, host, port)
viewers.yaml            # (existing, gitignored)

tests/
├── contract/
│   ├── test_boundaries.py        # (MODIFIED) extends: httpx only in metabase_client.py
│   ├── test_metabase.py          # (NEW) contracts for Card/Collection/Dashboard + MetabaseClient Protocol
│   └── (existing unchanged)
├── unit/
│   ├── test_metabase_client.py   # (NEW) unit tests for MetabaseClient using fake http layer
│   └── (existing unchanged)
└── integration/
    ├── test_metabase_integration.py  # (NEW) end-to-end test against Metabase + Forge + PG
    │                                  # — skipped without Docker Metabase + FORGE_API_KEY
    └── (existing unchanged)
```

**Structure Decision**: Single-project layout (extendiendo Option 1 de 001/002/003). El módulo nuevo `src/ai_engineering/metabase_client.py` aloja el cliente HTTP; contract models viven en `src/contracts/metabase.py`. El CLI acts como composition root y conecta everything vía callback `on_query_complete`. El rol `metabase_readonly` se crea via un helper nuevo en `src/data_access/adapters/postgres/roles.py` (engine-specific, confined per Principle III). No se modifica ningún contract existente — sólo se añaden nuevos.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. Principle IV se PRRSERVA: Metabase NO es un gate nuevo, es una capa de visualización best-effort que consume el SQL gobernado existente. La governance sigue living en `GovernedQueryProvider` (feature `003`); Metabase no la replica, no la weaken, no la bypassa. Table intentionally left empty.
