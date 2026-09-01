# Contract: MetabaseClient (HTTP Boundary)

**Feature**: 004-metabase-integration
**Date**: 2026-08-17
**Related**: [research.md](../research.md) Part A, G · [data-model.md](../data-model.md) · [pipeline_integration.md](./pipeline_integration.md)

> Define el contract del `MetabaseClient` — el wrapper tipado sobre la REST API de Metabase que vive en `src/ai_engineering/metabase_client.py`. Es el **ÚNICO** módulo que importa `httpx` (boundary enforced por `tests/contract/test_boundaries.py`). El cliente es best-effort: cualquier error HTTP se loguea y el pipeline continúa (FR-013).

## Interface boundary

### `MetabaseClient` — constructor + auth

| Method | Input | Output | Semantics |
|---|---|---|---|
| `__init__(config)` | `MetabaseConfig` | — | Crea un `httpx.Client` con la base_url y timeout. NO hace login aún (lazy). |
| `login() -> str` | — | `str` (session_token) | `POST /api/session` con admin creds; guarda token en `self._session_token`. Si ya hay token válido, no re-loga. |
| `_ensure_session() -> str` | — | `str` | Helper: si `session_token` es None, llama `login()`. |
| `_reauth_on_401(method, url, ...)` | request params | `httpx.Response` | Helper: si una request recibe 401, limpia el token, re-loga, y reintenta una vez. Fallido si el retry también 401. |

**Boundary rule**: el `MetabaseClient` importa `httpx` solo en este archivo. Es la ÚNICA clase que hace HTTP a Metabase. El `TextToSqlPipeline` NO lo conoce — el CLI lo injecta via callback. El boundary test (`tests/contract/test_boundaries.py` extendido) lo enforce.

### `MetabaseClient` — setup operations

| Method | Input | Output | Semantics |
|---|---|---|---|
| `is_setup_complete() -> bool` | — | `bool` | `GET /api/session/properties`; si `setup-token` es null → True (setup ya hecho). Usado por `metabase setup` para decidir si re-crear admin user. |
| `setup_initial(admin_email, admin_password) -> None` | `str, str` | — | `POST /api/setup` para crear el admin user. Llama `is_setup_complete()` primero para idempotency. Idempotente por diseño. |
| `create_db_connection(pg_config, role_password) -> int` | `PostgresConfig-like` + `str` | `int` (db_id) | `POST /api/database` para conectar Metabase a PostgreSQL usando el role `metabase_readonly`. Idempotente: si ya existe una DB connection con el mismo nombre, no re-crea (busca via `GET /api/database`). |
| `get_or_create_collection(name, parent_id) -> Collection` | `str, int \| None` | `Collection` | Busca si existe una colección con ese nombre (via `GET /api/collection` con el search tree); si no existe, `POST /api/collection`. Devuelve el model con `id` seteado. |

### `MetabaseClient` — card operations (used by the pipeline callback)

| Method | Input | Output | Semantics |
|---|---|---|---|
| `create_card(name, sql, collection_id, display, description) -> Card` | `str, str, int, str, str \| None` | `Card` | `POST /api/card` con payload `{name, dataset_query: {type: "native", native: {query: sql}}, display, description, collection_id}`. Devuelve el `Card` con `id` seteado por Metabase. |
| `get_or_create_dashboard(name, collection_id) -> Dashboard` | `str, int` | `Dashboard` | Busca dashboards con ese `name` en la colección; si no existe, `POST /api/dashboard`. |
| `add_card_to_dashboard(card_id, dashboard_id) -> DashboardItem` | `int, int` | `DashboardItem` | `POST /api/dashboard/{id}/dashcard` con payload `{card_id}`. Auto-position por Metabase. |
| `send_governed_query(response, viewer) -> Card \| None` | `TextToSqlResponse, SemanticViewer \| None` | `Card \| None` | **High-level helper**: toma el response del pipeline + el viewer, infiere el display_type, construye el `name` desde la pregunta NL original, y construye la card. Retorna `None` en caso de cualquier error (best-effort, FR-013). |

### `MetabaseClient` — operations for CLI (US4)

| Method | Input | Output | Semantics |
|---|---|---|---|
| `list_cards_in_collection(collection_id) -> list[Card]` | `int` | `list[Card]` | `GET /api/card?f=collection_id eq {id}`. Usado por `metabase status` para reportar conteo. |
| `delete_card(card_id) -> None` | `int` | — | `DELETE /api/card/{id}`. Usado por `metabase reset-cards`. |
| `get_health() -> dict[str, object]` | — | `dict` | `GET /api/health`. Usado por `metabase status` y por el health-check de Docker en compose. |
| `get_version() -> str` | — | `str` | `GET /api/session/properties` → `"version"` field. |

## Encounter — Session State (`MetabaseSession`)

El `MetabaseClient` mantiene un puntero opcional a `MetabaseSession` (cargado desde `.artifacts/metabase_state.json`):

- Métodos como `get_or_create_collection` y `create_card` usan este state para cache-evitar re-query la colección ID cada vez.
- El state se persiste en disco cada vez que el CLI exitosamente completa `metabase setup`.

## Boundary enforcement

| Rule | Enforcement |
|---|---|
| `httpx` imports confined to `metabase_client.py` | `tests/contract/test_boundaries.py::test_httpx_confined_to_metabase_client` (NEW) |
| `MetabaseClient` re-auth on 401 transparente | `tests/unit/test_metabase_client.py::test_reauth_on_401` |
| `send_governed_query` never raises — best-effort log only | `tests/unit/test_metabase_client.py::test_send_governed_query_best_effort` |
| `session_token` never logged | grep-check across `metabase_client.py` that no log call includes `self._session_token` |
| No direct PG adapter access from `metabase_client.py` | Extend `test_no_ai_engineering_direct_adapter_import` accordingly |

## Out of scope for this contract

- **Metabase Sandboxes / Group Policies** — Opción B de governance, no implementada (Opción A: SQL gobernado en cards).
- **Async HTTP** — el pipeline es sync. Deferred.
- **API keys persistentes** — se usa session token por simplicity y cross-version support.
- **Embedding de cards** — se usa Metabase UI standalone en `http://localhost:3000`.
