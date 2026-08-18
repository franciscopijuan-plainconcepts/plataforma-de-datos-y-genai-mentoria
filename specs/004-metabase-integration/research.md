# Research: Metabase Integration (Governed SQL Cards from Chat Sessions)

**Phase**: 0 (Outline & Research)
**Feature**: 004-metabase-integration
**Date**: 2026-08-17
**Status**: Complete — todos los NEEDS CLARIFICATION resueltos

> Este documento resuelve las 6 clarificaciones abiertas en `plan.md` Technical Context:
> 1. Cliente HTTP (`requests` vs `httpx`)
> 2. Versión de la imagen de Metabase y estrategia de pinning
> 3. Cómo inicializar el role `metabase_readonly` en PostgreSQL
> 4. Cómo injectar `MetabaseClient` en `TextToSqlPipeline` sin acoplamiento
> 5. Display type (chart type) de las cards creadas automáticamente
> 6. Metabase API idempotencia del setup

---

## Part A — HTTP Client: `httpx` over `requests`

### Decision: Reusar `httpx` (ya transitivo vía `openai`)

**Decision**: Usar `httpx` para todas las HTTP calls a la API de Metabase. NO se añade `requests` como nueva dependencia.

**Rationale**:

- **Ya está en el lockfile**: `httpx` es dependencia transitiva del SDK `openai` (ya en `pyproject.toml`). Reusarlo evita añadir una dependencia nueva — alineado con el principio YAGNI de la constitution.
- **API moderna**: `httpx` soporta HTTP/2 (no necesitamos aquí, pero futuro-proof), timeouts, auth, y sessions de forma similar a `requests`. Es el sucesor espiritual de `requests` para muchos proyectos modernos.
- **Type hints**: `httpx` tiene type hints mejores que `requests` (importante para `mypy --strict`).
- **Consistencia con el boundary**: el `openai` SDK ya usa `httpx` y está confinado a `src/ai_engineering/llm_client.py`; ahora confinamos `httpx` también a `src/ai_engineering/metabase_client.py` — un mismo modulo importa `httpx` una sola vez y el boundary test lo enforce (FR-024).

### Alternatives consideradas

- **`requests` (la opción más convencional para APIs REST)**: añade una dependencia nueva (~6 MB). Rechazado — `httpx` ya está, no hay razón para duplicar.
- **`urllib` (stdlib)**: demasiado low-level — manejo manual de timeouts, JSON encoding de bodies, etc. Rechazado.
- **`aiohttp` (async)**: el pipeline es sync (PostgresRepository usa psycopg sync, openai SDK sync); no necesitamos async HTTP. Redundaría. Rechazado.

### Implementación del boundary

```python
# src/ai_engineering/metabase_client.py — the ONLY module that imports httpx
import httpx

class MetabaseClient:
    def __init__(self, config: MetabaseConfig) -> None:
        self._config = config
        self._client = httpx.Client(base_url=config.host, timeout=30.0)
        self._session_token: str | None = None
```

El `tests/contract/test_boundaries.py` extiende con:

```python
def test_httpx_confined_to_metabase_client():
    """httpx imports stay in src/ai_engineering/metabase_client.py only."""
    # ... analog al test_openai_confined_to_ai_engineering()
```

---

## Part B — Metabase Image Version Pinning

### Decision: Pin del LTS de Metabase; `latest` no permitido

**Decision**: Usar el tag `metabase/metabase:v0.58-lts` en `docker-compose.yml`. Metabase provee tags LTS (Long Term Support) que son los más estables para integración.

**Rationale**:

- **Stability de la API**: Metabase tiene un historial de breaking changes entre major versions (e.g., renombraron `POST /api/card` a algo distinto en algún momento, o cambiaron el payload schema). Pinnear por major+minor evita sorpresas cuando alguien hace `docker pull` y recibe una version nueva.
- **Patch updates deseadas**: `v0.48-latest` permite recibir security patches y bugfixes de la serie v0.48.x sin cambiar el código.
- **Documentación de compatibilidad**: en `research.md` dejamos registrado que v0.48.x es la base de compatibilidad; cualquier bump debe ser explícito y testado.

### Alternatives consideradas

- **`metabase/metabase:latest`**: inaceptable — puede romper la feature cuando Metabase hace un release mayor.
- **Pin exacto `metabase/metabase:v0.48.7`**: demasiado estricto — perdés security patches.
- **Múltiples versiones soportadas**: complejo, no vale la pena para un ambiente local-only.

### Implementación

```yaml
# docker/docker-compose.yml (extracto del servicio metabase nuevo)
services:
  postgres:
    # ... existing
  metabase:
    image: metabase/metabase:v0.48-latest
    container_name: plataforma_metabase
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      # Externalized for reproducibility — DB connection is set up via API in `metabase setup`.
      MB_DB_TYPE: h2
      MB_DB_FILE: /metabase-data/metabase.db
      # Jetty port (the UI + API).
      MB_JETTY_PORT: "3000"  # documented as METABASE_PORT below
    ports:
      - "${METABASE_PORT:-3000}:3000"
    volumes:
      - plataforma_metabase_data:/metabase-data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/api/health"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 30s

volumes:
  plataforma_pgdata:
    name: plataforma_pgdata
  plataforma_metabase_data:
    name: plataforma_metabase_data
```

---

## Part C — Role `metabase_readonly` en PostgreSQL

### Decision: Crear en `metabase setup` via adapter existente (no migrations)

**Decision**: El comando `metabase setup` ejecuta DDL `CREATE ROLE` + `GRANT SELECT` vía el adapter PostgreSQL existente (no se añade migrations/DDL al `bootstrap` de PG). La lógica vive en `src/data_access/adapters/postgres/roles.py`, confined al package del adapter (Principle III).

**Rationale**:

- **No romper la baseline**: añadir DDL al `bootstrap` mezcla responsabilidades — Metabase no es parte del warehouse baseline. La feature 001 debe seguir siendo standalone (levantar el warehouse); Metabase es opcional y post-hoc.
- **Idempotencia**: el helper `ensure_metabase_readonly_role(repo)` chequea si el role existe (`SELECT 1 FROM pg_roles WHERE rolname = 'metabase_readonly'`) y lo crea sólo si falta. Los `GRANT` son idempotentes por construcción.
- **Engine-specific code confined**: el DDL es PostgreSQL-specific (la sintaxis `CREATE ROLE ... LOGIN PASSWORD ...` y `GRANT SELECT ON ALL TABLES IN SCHEMA public TO ...`); vive en el adapter, no en el contrato.
- **Future BigQuery**: si migras a BigQuery, este helper sería reemplazado por el equivalente de BigQuery (IAM role conditions). El `metabase setup` no llama a esto directamente — el helper es opcional y PG-specific.

### Implementación

```python
# src/data_access/adapters/postgres/roles.py — confined (Principle III)
from src.data_access.adapters.postgres.repository import PostgresRepository
from psycopg.sql import SQL, Identifier

_ROLE_NAME = "metabase_readonly"

def ensure_metabase_readonly_role(repo: PostgresRepository, password: str) -> None:
    """Idempotently create a read-only role for Metabase to use when querying PG."""
    with repo._conn.cursor() as cur:
        # Check if role exists; create if not.
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (_ROLE_NAME,))
        if cur.fetchone() is None:
            cur.execute(
                SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(Identifier(_ROLE_NAME)),
                (password,),
            )
        # Grant SELECT on existing tables (idempotent — GRANTs are repeatable).
        cur.execute(
            SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(Identifier(_ROLE_NAME))
        )
        # Ensure future tables (if you re-bootstrap with new tables) are also granted.
        cur.execute(
            SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(Identifier(_ROLE_NAME))
        )
    repo._conn.commit()
```

### Alternatives consideradas

- **Crear role en el `bootstrap` de la baseline**: mezcla responsabilidades, confunde el flow. Rechazado.
- **Crear role vía Metabase via SQL.steps al conectar**: hacky, no idempotente. Rechazado.
- **Crear role manualmente out-of-band (documentado en README)**: no reproducible. Rechazado.

---

## Part D — Inyección de `MetabaseClient` en `TextToSqlPipeline`

### Decision: Callback opcional `on_query_complete`

**Decision**: El `TextToSqlPipeline` constructor se extiende para aceptar un callback opcional:

```python
on_query_complete: Callable[[TextToSqlResponse, SemanticViewer | None], None] | None = None
```

Cuando NO es None, el pipeline lo invoca al final de un `ask` exitoso (después de construir el `TextToSqlResponse`). El callback decide qué hacer (enviar a Metabase, loggear, etc.) — el pipeline no conoce Metabase.

**Rationale**:

- **Cero acoplamiento**: el `TextToSqlPipeline` (en `src/ai_engineering/pipeline.py`) NO importa `metabase_client.py` (que venga de la feature 004). Sólo conoce el contract `Callable`. Esto preserva Principle II/III y permite extensibilidad futura (otro sink: un log file, una cola de events, etc.).
- **CLI como composition root**: el CLI encola el `MetabaseClient` dentro del callback via closure:
    ```python
    def on_complete(response, viewer):
        if metabase_client is None:
            return
        metabase_client.send_governed_query(response, viewer)
    pipeline = TextToSqlPipeline(..., on_query_complete=on_complete)
    ```
- **Composable y testeable**: en tests podés injectar un callback fake que capture el `response` para asserter, sin necesidad de un Metabase real.
- **Best-effort**: el callback encapsula su propio error handling (no propaga excepciones al pipeline). Si Metabase falla, se loguea y se mueve; el `ask` ya devolvió su resultado al usuario antes de invocar el callback.

### Alternatives consideradas

- **Subclassing `TextToSqlPipeline` con un `MetabasePipeline`**: rompe la simplicidad de un único `TextToSqlPipeline`; el usuario podría quiz tener las dos intenciones (con Metabase a veces, a veces no). Rechazado.
- **Import directo de `metabase_client` en `pipeline.py`**: viola Principle II/III (un dominio ai_engineering importa otro submodulo). Rejectado por la constitution.
- **Hook level OS (signals, etc.)**: overkill. Rechazado.
- **Async background queue para Metabase**: añadiría complejidad de threading sin un caso de uso claro (v2.1 es local-only, una sola `ask` por vez). Deferred a v3.0+ si se necesita concurrencia.

### Implementación del callback signature

```python
# src/ai_engineering/pipeline.py (modificación mínima)
OnQueryComplete = Callable[[TextToSqlResponse, "SemanticViewer | None"], None]

class TextToSqlPipeline:
    def __init__(
        self,
        ...,
        on_query_complete: OnQueryComplete | None = None,
    ) -> None:
        ...
        self._on_query_complete = on_query_complete

    def run(self, question: NLQuestion) -> TextToSqlResponse:
        ...
        response = ...
        _log_call(response, latency_ms, self._viewer)
        # INVOKE the callback at the END of a successful run
        if self._on_query_complete is not None:
            try:
                self._on_query_complete(response, self._viewer)
            except Exception as exc:
                # Never let Metabase (or any sink) break the pipeline
                _logger.warning("on_query_complete callback failed: %s", exc)
        return response
```

---

## Part E — Display Type Heuristics for Cards

### Decision: Heurística simple en 3 reglas (no IA, no configuración por card)

**Decision**: Cuando se crea una card de Metabase, el `display_type` se elige automáticamente con estas reglas en orden:

1. Si el resultado es una sola fila con una sola columna (single scalar): `display = "scalar"`. Ejemplo: `SELECT SUM("Sales") FROM Orders ...` → total label.
2. Si la query tiene `GROUP BY <dimension>` y devuelve N filas: `display = "bar"` si N ≤ 20, `display = "table"` si N > 20 (truncate). Ejemplo: `SELECT "Region", SUM("Sales") ... GROUP BY "Region"` → bar chart.
3. En cualquier otro caso: `display = "table"` (default safe). Ejemplo: `SELECT * FROM Orders ... LIMIT 10`.

**Rationale**:

- **Sin config por card**: el usuario viene del chat; no queremos que tenga que elegir el chart type. La heurística hace lo correcto para los 90% casos.
- **Analizar el SQL sería complejo y frágil**: parsear GROUP BY desde el SQL sería un mini-SQL parser. En vez de eso, miramos los **result rows** — si la query es una aggregation con GROUP BY, típicamente devuelve pocas filas (región, segmento, etc.); si devuelve muchas, mejor table.
- **Single-value = scalar**: si el row count=1 y cols=1, es seguro asumir que es un "total" → scalar display.

### Implementación

```python
# src/ai_engineering/metabase_client.py
def _infer_display_type(response: TextToSqlResponse) -> str:
    if not response.query_result:
        return "table"
    rows = response.query_result.rows
    if len(rows) == 1 and len(rows[0].data) == 1:
        return "scalar"
    if len(rows) <= 20 and response.generated_sql.sql.lower().count("group by") >= 1:
        return "bar"
    return "table"
```

### Alternatives consideradas

- **Usuario elige chart type via un flag `--chart bar`**: friction al UX. Solo útil en casos avanzados; deferred.
- **IA para elegir chart type según contenido**: overkill para v2.1 (un modelo extra, latencia, cost). Deferred a v3.0+ si la heurística no es suficiente.
- **Siempre `table`**: demasiado aburrido — pierde el "wow factor" de ver gráficas automaticas.
- **Usar Metabase auto-suggest**: Metabase tiene una API `/api/dataset` que puede inferir display type pero es opaca y puede stuckearse en `table`.

---

## Part F — Metabase Setup Idempotency

### Decision: Chequear `GET /api/session/properties` para detectar si el setup ya fue hecho

**Decision**: Cuando se ejecuta `metabase setup`, primero:

1. Llama `GET /api/session/properties` (endpoint público, no requiere auth).
2. Si la respuesta tiene `"setup-token": null` significa que el setup ya fue completado anteriormente → saltear el `POST /api/setup` (no re-crear admin user).
3. Si `setup-token` no es null → hacer el `POST /api/setup` con el admin user/pass de `.env`.
4. Persistir el state en `.artifacts/metabase_state.json` (admin_user, db_id, collection_id) para que comandos siguientes los reusen sin re-query.

**Rationale**:

- **Idempotencia reproducible**: si el usuario corre `metabase setup` dos veces (por ejemplo, después de un `teardown` sin `--remove-volume`), la segunda vez detecta que ya está configurado y no duplica el admin user (Metabase fallaría con "user exists").
- **Endpoint público**: `GET /api/session/properties` no requiere auth, así que el check funciona incluso antes de tener el session token.
- **Persistencia del state**: `.artifacts/metabase_state.json` actúa como cache entre invocations. Si falta (e.g., reseteaste el .artifacts), el comando retrive la info via API.

### State file schema

```json
{
  "configured_at": "2026-08-17T13:46:00Z",
  "admin_email": "admin@plataforma.local",
  "metabase_db_id": 2,
  "collection_id": 4,
  "metabase_version": "v0.48.7"
}
```

### Alternatives consideradas

- **Siempre re-run sin check**: duplica admin user, falla.
- **Check via try/catch en `POST /api/setup`**: frágil — el error no es consistente entre versiones de Metabase. El endpoint `properties` es estable.
- **Persistir en DB de Metabase misma**: Metabase no tiene un endpoint para esto; el JSON local es más simple.

---

## Part G — Metabase API Authentication (session tokens)

### Decision: Login via `POST /api/session`, store token en memoria, re-auth on 401

**Decision**: El `MetabaseClient.login()` hace `POST /api/session` con `{username, password}` de `.env`, obtiene un `session_token` (string), y lo guarda en memoria (`self._session_token`). En cada request subsecuente, se envía como `X-Metabase-Session: <token>` header. Si una request devuelve 401, el cliente limpia el token y re-intenta login una vez; si vuelve a fallar, la operación se propaga como error (best-effort, FR-013).

**Rationale**:

- **Problema de Metabase API keys**: Metabase v0.48 añadió API keys reales, pero la feature es nueva y no está documentada para todos los casos. Session tokens son más maduras y universalmente soportadas.
- **Mantener Simple**: el session token se mantiene en memoria (no persistente). El usuario no tiene que gestionar API keys rotativas en `.env`. Si Metabase restart, el token expira y el cliente re-loga transparentemente.
- **No se loguea el token**: FR-008 explicitado. El logger filtra el token.

### Alternatives consideradas

- **API keys persistentes (Metabase v0.48+)**: más correctamente Metabase's "API keys" feature persiste el key en DB y no expira. Pero la estabilidad cross-versiones no es clara; vortex el session token es más universal.
- **OIDC/JWT auth**: fuera de scope (v2.1 local-only).
- **Anonymous (sin auth)**: Metabase no permite un setup anónimo, todas las APIs POST requiren auth.

---

## Resumen

| # | Tema | Decisión | Alternativas rechazadas |
|---|---|---|---|
| A | Cliente HTTP | `httpx` (ya transitivo vía `openai`); confined a `metabase_client.py` | `requests`, `urllib`, `aiohttp` |
| B | Imagen Metabase | Tag `metabase/metabase:v0.48-latest` en docker-compose | `latest`, pin exacto, multiple versions |
| C | Role PG readonly | Helper `ensure_metabase_readonly_role` en PG adapter (no migrations) | bootstrap de PG, out-of-band manual, Metabase SQL steps |
| D | Inyección en pipeline | Callback `on_query_complete: Callable[...]` en constructor | Subclass pipeline, import directo, signals |
| E | Display type | Heurística: scalar (1×1), bar (group by ≤20), table default | flag `--chart`, IA, siempre table, Metabase auto-suggest |
| F | Setup idempotencia | Check `GET /api/session/properties` setup-token; persist en `.artifacts/metabase_state.json` | siempre re-run, try/catch en setup, DB Metabase |
| G | Auth | Session token via `POST /api/session`; re-auth on 401; no log token | Metabase API keys, OIDC/JWT, anon |
