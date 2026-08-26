# Data Model: Metabase Integration

**Feature**: 004-metabase-integration
**Date**: 2026-08-17
**Source**: Derived from `plan.md` Technical Context + `research.md` Parts A–G

> Este data model define los **contract models de Metabase** (Pydantic v2) que viven en `src/contracts/metabase.py`. NO modifica los contracts existentes (`data_access.py`, `text_to_sql.py`, `semantic_layer.py`) — esos siguen sin cambios. La extensión al runtime es:
> 1. Un callback `on_query_complete` (typed) en `TextToSqlPipeline` (ver [contracts/pipeline_integration.md](./contracts/pipeline_integration.md)).
> 2. Una nueva clase `MetabaseClient` en `src/ai_engineering/metabase_client.py` que es el ÚNICO módulo que importa `httpx`.
> 3. Un helper `ensure_metabase_readonly_role` en `src/data_access/adapters/postgres/roles.py` (engine-specific, confined per Principle III).

## Entities (Metabase Contracts)

Todos los modelos viven en `src/contracts/metabase.py` (Pydantic v2, frozen, tipos explícitos). Son **engine-neutral** — ningún modelo referencia `psycopg`, `httpx`, o la API de Metabase; esos viven solo en `metabase_client.py`.

### 1. `MetabaseConfig`

Configuración del cliente Metabase, cargada desde env vars.

| Field | Type | Env Var | Default | Notes |
|---|---|---|---|---|
| `host` | `str` | `METABASE_HOST` | `http://localhost:3000` | URL base de la instancia de Metabase. |
| `admin_email` | `str` | `METABASE_ADMIN_EMAIL` | `(required)` | Email del admin user creado en setup; usado para `POST /api/session`. |
| `admin_password` | `str` | `METABASE_ADMIN_PASSWORD` | `(required)` | Password del admin user. Nunca se loguea (FR-008). |
| `port` | `int` | `METABASE_PORT` | `3000` | Puerto de la API/UI de Metabase. Usado para Sanitize/validate host. |
| `collection_name` | `str` | — | `"Chat Sessions"` | Nombre de la colección donde se crean las cards generadas. Hard-coded default. |
| `db_name` | `str` | — | `"Plataforma PostgreSQL"` | Nombre con el que Metabase referencia la database connection a PG. Hard-coded default. |

**Validation rules**:
- `admin_email` MUST no ser vacío.
- `admin_password` MUST no ser vacío.
- `host` MUST parsear como URL válida con esquema `http` o `https`.

### 2. `Card`

Una card de Metabase (native SQL query) creada por el pipeline.

| Field | Type | Notes |
|---|---|---|
| `id` | `int` | ID asignado por Metabase al crear la card (no se setea en creación, viene del response). |
| `name` | `str` | Título descriptivo, derivado de la pregunta NL original (truncated a 140 chars). |
| `sql` | `str` | El SQL **ya gobernado** (con `WHERE "Region" IN (...)` inyectado por el resolver). Este es el campo critico para constitution Principle IV. |
| `collection_id` | `int` | ID de la colección donde vive la card. Típicamente el de "Chat Sessions". |
| `display` | `Literal["scalar", "table", "bar", "line", "area"]` | Chart type para visualización. Elegido por heurística (research.md Part E). |
| `description` | `str \| None` | Metadata del origen: `viewer_id`, `session_id` si aplica, timestamp. Lo usa el reviewer para trazabilidad. |
| `created_at` | `datetime` | Timestamp UTC de creación (del response de Metabase). |

**Validation rules**:
- `sql` MUST no ser vacío.
- `display` MUST ser uno de los valores del Literal.
- `name` MUST no exceder 140 chars (Metabase rechaza más largos).

### 3. `Collection`

Una colección de Metabase (carpeta lógica donde viven las cards).

| Field | Type | Notes |
|---|---|---|
| `id` | `int` | ID asignado por Metabase. |
| `name` | `str` | "Chat Sessions" para el default. |
| `parent_id` | `int \| None` | ID de la colección padre (Metabase soporta nested). None = root. |
| `location` | `str` | Path de la colección en el arbol (e.g., `/Chat Sessions/`). |

**Validation rules**:
- `name` MUST no ser vacío.
- `parent_id` puede ser None (root collection).

### 4. `Dashboard`

Un dashboard de Metabase agrupando varias cards.

| Field | Type | Notes |
|---|---|---|
| `id` | `int` | ID asignado por Metabase. |
| `name` | `str` | "Session: <session_id>" cuando se usa `--session <id>`. |
| `collection_id` | `int` | ID de la colección padre (típicamente "Chat Sessions"). |
| `ordered_items` | `list[DashboardItem]` | Cards en el dashboard, en orden. |

**Validation rules**:
- `name` MUST no ser vacío.
- `ordered_items` puede ser vacío (un dashboard recien creado sin items).

### 5. `DashboardItem`

Una card colocada en un dashboard (Metabase llama esto "dashboard card" o "dashcard").

| Field | Type | Notes |
|---|---|---|
| `id` | `int` | ID del dashcard. |
| `card_id` | `int` | ID de la card referenciada. |
| `dashboard_id` | `int` | ID del dashboard donde vive. |
| `position` | `tuple[int, int]` | `(row, col)` en la grilla del dashboard. Auto-calc por Metabase (el cliente solo especifica el card_id). |

**Validation rules**:
- `card_id` MUST ser positivo (Metabase asigna IDs ≥ 1).
- `position` es informativa; el cliente no lo setea al insertar.

### 6. `MetabaseSession`

El state del setup de Metabase, persistido en `.artifacts/metabase_state.json` para idempotency entre comandos.

| Field | Type | Notes |
|---|---|---|
| `configured_at` | `datetime` | Timestamp UTC del último `metabase setup` exitoso. |
| `admin_email` | `str` | Confirmación del admin user creado. |
| `metabase_db_id` | `int` | ID de la database connection a PostgreSQL (retornado por `POST /api/database`). |
| `collection_id` | `int` | ID de la colección "Chat Sessions" (retornado por `POST /api/collection`). |
| `metabase_version` | `str` | Version de Metabase reportada por `GET /api/session/properties`. |

**Validation rules**:
- `metabase_db_id` MUST ser positivo.
- `collection_id` MUST ser positivo.

### 7. `MetabaseClientConfig` (helper)

Wrapper interno de configuraciones que se pasan al `MetabaseClient` constructor en runtime.

| Field | Type | Notes |
|---|---|---|
| `config` | `MetabaseConfig` | URL + creds. |
| `state` | `MetabaseSession \| None` | El state cacheado del setup actual (None si aún no se hizo). |

## State Definitions

Sin state machines complejos:

- `MetabaseClient._session_token`: in-memory only, se pierde al cerrar el proceso. Re-login transparente on 401.
- `.artifacts/metabase_state.json`: persistente entre invocations del CLI; refleja el state del setup.
- La governance NON-NEGOTIABLE: el `sql` field del `Card` NO es modificado por Metabase — siempre es el SQL gobernado que el pipeline envía. Metabase no tiene forma de modificar ese SQL en la card (es una native query literal).

## Diagram (runtime flow)

```
ask question
  ↓
TextToSqlPipeline.run(question)
  ├─ build_prompt + LLM → GeneratedSql
  ├─ SqlValidator → ValidationResult
  └─ if accepted: GovernedQueryProvider.execute_readonly_query(sql, table_def)
         ├─ SemanticQueryResolver.apply_rls(sql, viewer) → governed_sql  ← RLS injected here
         └─ PostgresRepository.execute_readonly_query(governed_sql) → QueryRow[]
  ↓ returns TextToSqlResponse with governance context (viewer_id, governed SQL, rows)
  ↓ on_query_complete callback (if Metabase enabled):
  ↓
MetabaseClient.send_governed_query(response, viewer)
  ├─ infer display type (scalar/bar/table) from response shape
  ├─ build Card(name from NL question, sql = response.query_result.sql ← governed SQL,
  │              collection_id from state, display, description with viewer_id)
  └─ POST /api/card → create card in Metabase
```

## Validation Rules summary

Las validaciones se enforce en los modelos Pydantic v1 frozen:

1. `MetabaseConfig.from_env()` raise si `METABASE_ADMIN_EMAIL` o `METABASE_ADMIN_PASSWORD` faltan (FR-013 fail-fast).
2. `Card.sql` no empty; `Card.display` en el Literal closed set; `Card.name` maxlength.
3. Ninguna operación de Metabase (crear card, crear colección, login) puede blockear el pipeline — todas se cautchan y se loguean (FR-013 best-effort).
