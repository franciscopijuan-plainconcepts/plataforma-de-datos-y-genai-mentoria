# Feature Specification: Metabase Integration (Governed SQL Cards from Chat Sessions)

**Feature Branch**: `004-metabase-integration`

**Created**: 2026-08-17

**Status**: Draft

**Input**: User description: "Agregar una conexión con https://github.com/metabase/metabase a este proyecto. Se levanta un servicio de Metabase y, en una sesión del chat del LLM, poder agregar las consultas SQL que se van generando al servicio de Metabase, para poder ver las gráficas y dashboards a partir de esas consultas. El setup de Metabase debe ser reproducible desde clean clone. La integración con el LLM debe ser automática en cada `ask` exitoso. La gobernanza RLS debe respetarse: Metabase no puede bypassar el Semantic Layer."

## Scope Summary

Esta especificación define el milestone **v2.1** de la Plataforma de Datos y GenAI: una integración con **Metabase** que permite visualizar las consultas SQL generadas por el pipeline de Text-to-SQL como cards/dashboards en una instancia local de Metabase, respetando estrictamente la gobernanza RLS implementada en la feature `003-semantic-layer-v1`.

### Entregables concretos (v2.1)

- **Servicio de Metabase en Docker**: una nueva entrada en `docker/docker-compose.yml` que levanta Metabase junto a PostgreSQL, con volúmenes persistentes y healthcheck.
- **Setup automático y reproducible**: a través de un comando `metabase setup`, se bootstrapa Metabase vía API REST — crea el admin user, configura la database connection a PostgreSQL, y crea una colección donde vivirán las cards generadas. Reproducible desde clean clone con un solo comando (al igual que `bootstrap` para PostgreSQL).
- **Módulo `MetabaseClient`**: un wrapper Python tipado sobre la REST API de Metabase que expone operaciones de crear/actualizar cards (native SQL queries), colecciones y dashboards.
- **Integración con el `TextToSqlPipeline`**: al final de cada `ask` exitoso (cuando una query genera SQL validado + filas), el SQL **ya gobernado** (con `WHERE "Region" IN (viewer.regions)` inyectado por el resolver) se envía a Metabase como una card nueva. Esto garantiza que cualquier persona que vea la card en Metabase también vea filas dentro del scope de gobernanza del viewer que originó la consulta.
- **Comandos CLI**: `metabase setup` (bootstrap), `metabase status` (health check), y extensión del comando `ask` existente con flag `--send-to-metabase` (default `true` cuando Metabase está corriendo).

### Explicitly out of scope

- **Metabase como herramienta de autoría de queries ad-hoc** — Metabase se usa solo como visualization layer; las consultas se originan SIEMPRE en el pipeline de Text-to-SQL con RLS ya aplicado. No se expone el editor de SQL de Metabase para escribir consultas desde cero (eso rompería la gobernanza).
- **Metabase Sandboxes / Group Policies nativas** — sería una replicación de la RLS nativa de Metabase. No se implementa en v2.1; el approach es que el SQL que llega a las cards YA tiene la RLS aplicada (Opción A de la constitution Principle IV).
- **Embedding de dashboards de Metabase en otra UI** — fuera de scope; se usa Metabase como aplicación standalone en `http://localhost:3000`.
- **Auth real (OIDC/JWT) sobre Metabase** — fuera de scope; el admin user se crea con credenciales locales en `.env` (igual que PostgreSQL). Deferred a v3.0+.
- **Multi-tenant en Metabase** — fuera de scope.
- **Migración a BigQuery** — sigue fuera de scope (PostgreSQL local).
- **Cambio del Semantic Layer o de los contracts RLS** — fuera de scope; esta feature consume el `GovernedQueryProvider` existente, no lo modifica.

### Roadmap context

| Hito | Estado | Feature |
| --- | --- | --- |
| M0 | Completado | `001-data-genai-platform-baseline` (warehouse + dictionary) |
| M1 | Completado | `002-text-to-sql-v1` v1.0 (pipeline NL→SQL sobre Orders) |
| M2 | Completado | `002-text-to-sql-v1` v1.1 (logging + sanity-check) |
| M3 | Completado | `003-semantic-layer-v1` (Semantic Layer + RLS Governance) |
| **M3.1** | **En curso (esta feature)** | **`004-metabase-integration` (visualization layer respetando RLS)** |
| M4 | Pendiente | v3.0: RBAC column-level + People.Region taxonomy + Audit + auth real |

## Semantic Layer Assessment (critical context)

La pregunta implícita: "¿rompe Metabase la gobernanza RLS (Principle IV NON-NEGOTIABLE)?"

**Respuesta: No, si se integra correctamente.** Metabase se conectará a PostgreSQL con un usuario read-only, pero **las cards que el pipeline crea SIEMPRE contienen el SQL ya gobernado** — es decir, el SQL que el `GovernedQueryProvider` ya pasó por el `SemanticQueryResolver.apply_rls()` antes de enviarlo a Metabase. La constitución Principle IV se respeta porque:

1. **El SQL de cada card ya tiene `WHERE "Region" IN (viewer.regions)` inyectado** — no importa qué usuario haga clic en la card en Metabase, los datos que ve están scopeados por el viewer que originó la consulta.
2. **Metabase no puede crear nuevas queries que bypassen RLS** — las APIs de Metabase para escribir SQL ad-hoc NO se exponen en esta integración; el pipeline de Text-to-SQL es el único origen de SQL.
3. **El usuario de BD de Metabase es read-only** — incluso si alguien intentara escribir SQL ad-hoc vía Metabase (lo cual está fuera de scope y desrecomendado), el role de PostgreSQL solo permite SELECT.

Esto es la "Opción A" (SQL gobernado en las cards) que el usuario confirmó durante la clarificación de esta spec.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Levantar Metabase y conectarse al warehouse (Priority: P1) 🎯 MVP

Un Data Engineer o AI Engineer quiere tener Metabase corriendo localmente, conectada al warehouse PostgreSQL de la plataforma, con un setup reproducible desde clean clone del repo.

**Why this priority**: Es el cimiento — sin Metabase andando y conectada, no hay nada que integrar. Es el MVP que entrega valor independiente: aunque todavía no integremos el chat, ya permite abrir Metabase y ver el warehouse.

**Independent Test**: Ejecutar `uv run python -m src.cli.main metabase setup` desde clean clone (tras `bootstrap`) → abrir `http://localhost:3000` → ver el login completo (sin setup wizard) → ver PostgreSQL como database conectada → ver la colección "Plataforma de Datos y GenAI / Chat Sessions" creada.

**Acceptance Scenarios**:

1. **Given** el warehouse PostgreSQL está corriendo (`validate` pasa), **When** el usuario ejecuta `metabase setup`, **Then** el comando trae arriba el container de Metabase en Docker (si no está corriendo), espera el healthcheck, y via API REST hace el setup inicial (create admin user, configure DB connection to PostgreSQL, create collection).
2. **Given** el setup completó, **When** un reviewer abre `http://localhost:3000` y se loguea con las credenciales de `.env`, **Then** ve el dashboard de Metabase sin el setup wizard inicial, con PostgreSQL apareciendo en la lista de databases conectadas.
3. **Given** el setup completó, **When** se listan las colecciones via API, **Then** existe una colección llamada "Plataforma de Datos y GenAI / Chat Sessions" donde vivirán las cards generadas por el pipeline.
4. **Given** Metabase ya está configurada, **When** el usuario corre `metabase setup` de nuevo (segunda vez), **Then** el comando es idempotente: detecta que Metabase ya está configurada y no re-crea el admin user ni la DB connection ni la colección; devuelve un mensaje de "already configured".
5. **Given** un clean clone del repo, **When** un contributor nuevo ejecuta `bootstrap` y luego `metabase setup`, **Then** alcanza el mismo estado de Metabase (sin pasos manuales) — reproducible desde cero en una sola operación.

---

### User Story 2 — Enviar queries gobernadas a Metabase desde `ask` (Priority: P1)

Un AI Engineer hace una consulta natural-language via `ask` y quiere ver la gráfica en Metabase plus la tabla de resultados, sin friction extra.

**Why this priority**: Es la pieza central de la feature — sin esto, Metabase es solo un container corriendo sin conectarse con el pipeline. P1 porque es lo que entrega el valor real de esta integración.

**Independent Test**: Correr `ask --viewer marilene_rousseau "total sales by region"` (con Metabase configurada) → abrir Metabase → ver una card nueva en la colección "Chat Sessions" con el SQL gobernado (incluye `WHERE "Region" IN ('Caribbean')`) y un gráfico de barras por región.

**Acceptance Scenarios**:

1. **Given** Metabase está configurada (US1 completado) y el pipeline de Text-to-SQL + RLS está activo, **When** el usuario ejecuta `ask --viewer marilene_rousseau "total sales by region"`, **Then** el pipeline corre normalmente (valida SQL, aplica RLS vía `GovernedQueryProvider`, ejecuta, retorna filas) **Y además** crea una card nueva en Metabase con el SQL gobernado (la versión con `WHERE "Region" IN ('Caribbean')` ya inyectada) como `native query`.
2. **Given** una card creada en Metabase desde un `ask` previo, **When** un reviewer hace clic en la card desde la UI de Metabase, **Then** Metabase ejecuta la card y devuelve un gráfico/tabla con SOLO los datos de las regiones del viewer que originó la consulta (Caribbean en este caso) — confirma que el SQL gobernado respeta la RLS incluso al re-ejecutarse desde Metabase.
3. **Given** el usuario hace una consulta en español o inglés, **When** el `ask` exitoso crea la card, **Then** la card incluye metadata útil: título descriptivo (derivado de la pregunta original), viewer_id en la descripción (para trazabilidad), timestamp.
4. **Given** el usuario pasa el flag `--no-metabase` al `ask`, **When** se ejecuta, **Then** NINGUNA card se crea en Metabase — el `ask` funciona normalmente pero no envía nada.
5. **Given** Metabase no está corriendo o falla el envío (red, auth, etc.), **When** un `ask` exitoso intenta enviar la card, **Then** el `ask` NO falla por esto — la card no se crea, se loguea un warning en `.artifacts/text_to_sql.log`, y el usuario recibe sus filas normalmente. Metabase es best-effort, no bloquea.

---

### User Story 3 — Ver y consolidar las cards de una sesión en un dashboard (Priority: P2)

Un AI Engineer quiere ver todas las consultas de una sesión agrupadas en un solo dashboard de Metabase, para revisar el "conversation flow" e iterar.

**Why this priority**: P2 porque amplifica US2: cada `ask` individual ya funciona (US2 entrega ese MVP). US3 añade la noción de "sesión" para agrupar las cards en un dashboard, lo que hace el flujo de iteración sobre las consultas más natural.

**Independent Test**: Correr varias `ask` con un mismo session-id (o una sesion marcada con `--session <id>`) → abrir Metabase → ver un dashboard con todas las cards de esa sesión en una sola vista.

**Acceptance Scenarios**:

1. **Given** el usuario pasa `--session my-bi-review-2026-08` al `ask`, **When** la card se crea en Metabase, **Then** se busca/crea un dashboard con nombre "Session: my-bi-review-2026-08" en la colección "Chat Sessions", y la card nueva se agrega a ese dashboard (en posición secuencial).
2. **Given** varias `ask` ejecutadas sin `--session`, **When** se revisa Metabase, **Then** cada `ask` crea su card individual en la colección "Chat Sessions" sin un dashboard agrupador (comportamiento default).
3. **Given** un dashboard de sesión ya existe, **When** se agrega una nueva card a la sesión, **Then** el dashboard se actualiza para incluir la card nueva (en la próxima posición disponible).
4. **Given** un usuario recarga el dashboard, **When** hace clic en una card individual, **Then** cada card sigue mostrando SOLO los datos del viewer que originó la consulta (RLS preserved a través del dashboard).

---

### User Story 4 — Operar Metabase por CLI (Priority: P2)

Un Data Engineer o AI Engineer quiere operar Metabase desde la CLI sin entrar a la UI: ver status, hacer setup, y limpiar cards de testing.

**Why this priority**: P2 — se construye sobre US1 (setup) y US2 (cards). Es la capa de operación que facilita el workflow local (especialmente en CI/smoke tests).

**Independent Test**: Correr `metabase status` → ver状态的 readable (up/down, # cards, admin user). Correr `metabase setup` → confirmar idempotencia. Correr `metabase teardown` → bajar Metabase y limpiar volumen (similar a `teardown` para PG).

**Acceptance Scenarios**:

1. **Given** Metabase está corriendo, **When** el usuario ejecuta `metabase status`, **Then** se imprime un resumen: container status, DB connection status, número de cards en la colección "Chat Sessions", admin user name.
2. **Given** Metabase no está corriendo, **When** el usuario ejecuta `metabase status`, **Then** se imprime "Metabase is not running" con instrucciones claras para iniciarlo (`metabase setup`).
3. **Given** Metabase está corriendo, **When** el usuario ejecuta `metabase teardown --remove-volume`, **Then** el container se detiene y, si se pasa la flag, el volumen de Metabase se elimina (similar a `teardown --remove-volume` para PG). Las cards y la config se pierden si se quita el volumen.
4. **Given** el usuario quiere resetear el setup sin bajar el container, **When** ejecuta `metabase reset-cards`, **Then** todas las cards de la colección "Chat Sessions" se eliminan (no toca el admin user ni la DB connection).

---

### Edge Cases

- **¿Qué pasa si Metabase no está corriendo cuando se hace `ask`?** El pipeline corre normalmente; la integración con Metabase se saltea silenciosamente con un warning en el log. El usuario no ve error en su output del `ask`.
- **¿Qué pasa si la API de Metabase devuelve un error (auth expirada, rate limit, etc.)?** El pipeline no falla; se loguea el error y se continúa con el resultado del `ask` ya devuelto. Best-effort.
- **¿Qué pasa si el SQL del LLM no es un SELECT válido para Metabase?** Si el `SqlValidator` lo aprobó, Metabase lo acepta (Metabase native queries acepta cualquier SQL que Postgres corra). Si falla al ejecutarse en Metabase (e.g., schema drifted), se loguea pero el `ask` ya devolvió resultados al usuario.
- **¿Qué pasa si el viewer cambia entre dos `ask` en una misma sesión?** Cada card tiene su propio SQL gobernado con el viewer de esa llamada. Una sesión puede tener cards con diferentes viewers; el dashboard no "mezcla" governance, cada card es independiente.
- **¿Qué pasa si el usuario hace `ask` con `--allow-full-access`?** La card SI se envía a Metabase (el SQL es el original sin RLS, pero eso es lo que el viewer pidió). El log de la card debe registrar `gov_bypass=True` para auditoría — claramente identifiesc que esa card no está RLS-scoped.
- **¿Qué pasa si dos sesiones distintas eligen el mismo session-id?** Idempotencia: si el dashboard ya existe, se agrega la card al dashboard existente. Si no existe, se crea. No hay collision de sesión.
- **¿Qué pasa si Docker no está disponible?** `metabase setup` y `metabase status` fallan rápido con error claro (FR-013 pattern, igual que `bootstrap` de la baseline).

## Requirements *(mandatory)*

### Functional Requirements

#### Metabase Container (US1)

- **FR-001**: El sistema MUST proveer una entrada de Metabase en `docker/docker-compose.yml` que levanta la imagen oficial de Metabase junto a PostgreSQL, con volúmenes persistentes y healthcheck.
- **FR-002**: El sistema MUST externalizar las variables de configuración de Metabase (port, admin email, admin password, DB connection details) en variables de entorno via `.env` (siguiendo el patrón de la baseline 001).
- **FR-003**: El sistema MUST proveer un comando CLI `metabase setup` que llame a la REST API de Metabase para hacer: (a) completar el setup inicial via `POST /api/setup` creando el admin user; (b) crear la database connection a PostgreSQL via `POST /api/database`; (c) crear la colección "Plataforma de Datos y GenAI / Chat Sessions" via `POST /api/collection`.
- **FR-004**: El `metabase setup` MUST ser idempotente — si detecta que ya hay un admin user configurado o la database connection ya existe, saltea esa parte sin fallar (similar a `bootstrap` de PG).
- **FR-005**: El `metabase setup` MUST fallar rápido con un error claro (patrón FR-013) si Docker no está disponible, si Metabase no responde al healthcheck en un timeout razonable, o si la API call devuelve error.

#### Metabase Client (US2)

- **FR-006**: El sistema MUST implementar un `MetabaseClient` (clase Python tipada) que envuelva la REST API de Metabase y exponga métodos: `login() -> session_token`, `get_or_create_collection(name, parent_id) -> Collection`, `create_card(name, sql, collection_id, display_type) -> Card`, `add_card_to_dashboard(card_id, dashboard_id) -> DashboardItem`, `get_or_create_dashboard(name, collection_id) -> Dashboard`.
- **FR-007**: El `MetabaseClient` MUST ser la ÚNICA clase que interactúa con Metabase via HTTP — ningún otro módulo hace HTTP calls a Metabase. Boundary test lo enforce (constitution Principle II/III).
- **FR-008**: Las credenciales de Metabase (admin email, admin password, endpoint URL) MUST cargarse desde variables de entorno; el cliente nunca loguea el session_token ni el password.
- **FR-009**: El `MetabaseClient` MUST poder autenticarse una vez al iniciarse y re-autenticarse si la sesión expira (401 response → refresh del token).

#### Pipeline Integration (US2)

- **FR-010**: El `TextToSqlPipeline` MUST ser extendido para aceptar un `MetabaseClient | None` opcional (o un callback de "post-query") que, cuando está presente, se invoca al final de un `ask` exitoso.
- **FR-011**: El SQL enviado a Metabase MUST ser la versión **ya gobernada** por el `GovernedQueryProvider` (es decir, con `WHERE "Region" IN (viewer.regions)` inyectado). El cliente recibe el SQL gobernado, no el SQL crudo del LLM.
- **FR-012**: Cuando una `ask` con Metabase activo termina exitosamente, el sistema MUST crear una card en Metabase con: (a) el SQL gobernado como native query; (b) un título descriptivo derivado de la pregunta original NL; (c) la viewer_id en la descripción; (d) timestamp.
- **FR-013**: La integración MUST ser best-effort — cualquier error de Metabase (red, auth, 400) se loguea como warning en `.artifacts/text_to_sql.log` y NO rompe el flujo del `ask`. El usuario recibe sus filas normalmente.
- **FR-014**: El usuario MUST poder deshabilitar el envío a Metabase con `--no-metabase` flag en el comando `ask`.

#### Sessions (US3)

- **FR-015**: El comando `ask` MUST aceptar un flag opcional `--session <id>` que agrupa las cards de esa invocación bajo un dashboard de nombre "Session: <id>" en la colección "Chat Sessions".
- **FR-016**: Si el dashboard ya existe para esa sesión, la card nueva se agrega al dashboard existente (en la siguiente posición disponible). Si no existe, se crea.
- **FR-017** (out of v2.1 scope, listed for v3.0+): Multi-user sessioning (different viewers en una sesión). Cada `ask` mantiene su propio viewer; el dashboard no asume un único viewer.

#### CLI (US4)

- **FR-018**: El sistema MUST proveer comando `metabase status` que imprime: container status, version de Metabase, DB connection status, número de cards en la colección "Chat Sessions", admin user name.
- **FR-019**: El sistema MUST proveer comando `metabase teardown` (con `--remove-volume` option, similar a `teardown` de PG) que detiene y elimina el container de Metabase (y opcionalmente el volumen).
- **FR-020**: El sistema MUST proveer comando `metabase reset-cards` que elimina todas las cards de la colección "Chat Sessions" sin tocar el admin user ni la DB connection.

#### Arquitectura

- **FR-021**: El `MetabaseClient` y sus helpers MUST vivir en `src/ai_engineering/metabase_client.py` (submódulo del dominio AI Engineering porque forma parte del "post-query pipeline" del Text-to-SQL).
- **FR-022**: El `MetabaseClient` NO MUST importar `psycopg`, `openai`, `pandas`, o adapters internos — solo `httpx` (o `requests`) para HTTP y los typed contracts en `src/contracts/metabase.py` (Pydantic v2).
- **FR-023**: Los contract models de Metabase (Card, Collection, Dashboard, MetabaseConfig) MUST vivir en `src/contracts/metabase.py` (Pydantic v2 frozen, typed-boundaries per constitution Principle I).
- **FR-024**: El boundary test en `tests/contract/test_boundaries.py` MUST ser extendido para verificar: (a)_requests/httpx imports confined to `src/ai_engineering/metabase_client.py`; (b) el `MetabaseClient` no conoce el adapter de PostgreSQL (solo conoce la URL de la API de Metabase); (c) el `TextToSqlPipeline` no llama directamente al `MetabaseClient` — lo recibe inyectado.
- **FR-025** (residual governance): El usuario de PostgreSQL que Metabase usa para conectar MUST ser un role read-only con grants SOLO de SELECT en las tablas `Orders`, `Returns`, `People` (defense-in-depth además del SQL gobernado en las cards).

### Key Entities

- **`MetabaseConfig`**: Configuración cargada desde env vars: `METABASE_HOST` (default `http://localhost:3000`), `METABASE_ADMIN_EMAIL`, `METABASE_ADMIN_PASSWORD`, `METABASE_DB_ID` (el ID de la database connection a PG, se obtiene en setup), `METABASE_COLLECTION_ID` (id de la colección "Chat Sessions").
- **`MetabaseClient`**: El wrapper tipado sobre la API REST. Métodos: `login`, `setup_initial`, `get_or_create_collection`, `create_card`, `get_or_create_dashboard`, `add_card_to_dashboard`, `list_cards_in_collection`, `delete_card`.
- **`Card`**: Modelo Pydantic que representa una card de Metabase: `id`, `name`, `dataset_query` (con `native.query` = el SQL gobernado), `collection_id`, `display` (`scalar`/`table`/`bar`/`line`/`area`), `description` (incluye viewer_id + timestamp).
- **`Collection`**: `id`, `name`, `parent_id`, `location`.
- **`Dashboard`**: `id`, `name`, `collection_id`, `ordered_items` (list of `DashboardItem`).
- **`SessionDashboard`**: Wrapper de `Dashboard` con metadata de la sesión: `session_id`, `cards_count`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un contributor puede ejecutar `metabase setup` desde clean clone del repo (tras `bootstrap` para PG) y alcanzar un estado donde Metabase esté corriendo, configurada, y con la colección "Chat Sessions" creada — en menos de 2 minutos (incluyendo download de la imagen de Docker si no está cached).
- **SC-002**: Una vez configurada, abrir `http://localhost:3000`, loguearse con las credenciales de `.env`, y ver PostgreSQL en la lista de databases conectadas (no wizard de setup).
- **SC-003** (governance NON-NEGOTIABLE): Cualquier card creada por el pipeline en Metabase contiene SQL con `WHERE "Region" IN (...)` ya inyectado — verificado que al re-ejecutar la card desde Metabase, los resultados están scopeados por el viewer original. La gota: si un viewer con regiones `[Caribbean]` origina una card,electronics re-ejecutarla desde Metabase devuelve SOLO filas con `Region = 'Caribbean'`.
- **SC-004**: El `ask --viewer marilene_rousseau "total sales by region"`, correlation éxito, crea una card nueva con un nombre descriptivo en la colección "Chat Sessions" de Metabase, y esa card es visible y ejecutable desde la UI de Metabase.
- **SC-005**: Si se pasan múltiples `ask --session my-bi-review` seguidos, todas las cards se agrupen en un dashboard "Session: my-bi-review" en Metabase, cada card en posición secuencial.
- **SC-006**: `ask` con `--no-metabase` NO crea ninguna card en Metabase; el ask funciona normalmente.
- **SC-007** (resiliencia): Si Metabase no está corriendo o falla el envío, `ask` funciona correctamente y devuelve filas; el error se loguea en `.artifacts/text_to_sql.log`; no se rechaza el comando.
- **SC-008**: `metabase status`, `metabase teardown`, y `metabase reset-cards` funcionan desde la CLI y son reproducibles.
- **SC-009** (constitucional): El role de PostgreSQL que Metabase usa para conectarse es read-only; si Motors intenta escribir, falla. Defense-in-depth además del SQL gobernado.
- **SC-010**: `mypy --strict` pasa con cero errores en el código nuevo; `requests`/`httpx` stays confined to `src/ai_engineering/metabase_client.py`; boundary test extended lo enforce.

## Assumptions

- **Prerequisitos**: Features `001` (baseline), `002` (Text-to-SQL), y `003` (Semantic Layer) completas y mergeadas. El warehouse y la RLS están funcionando.
- **Metabase versión**: Se usa la imagen oficial `metabase/metabase:latest` (o un tag estable específico definido en `docker/docker-compose.yml`). La API v0.x que usa esta feature es estable en versiones recientes (v0.48+).
- **Local-only**: Al igual que las features anteriores, esto corre localmente en Docker. No se despliega Metabase a un servidor externo en v2.1.
- **Single-tenant Metabase**: Un admin user y una colección "Chat Sessions" única. No hay multi-tenant en Metabase en v2.1.
- **Auth Metabase básica**: El administrativo user se crea con credenciales locales en `.env` (igual que PG). No hay OIDC/JWT sobre Metabase — deferred a v3.0+.
- **Best-effort integration**: La integración con Metabase NO es un gate constitucional — es una capa de visualización opcional. Si Metabase no está, el pipeline sigue cumpliendo Principle IV. **Distinto** del `GovernedQueryProvider`, que SÍ es NON-NEGOTIABLE.
- **Read-only DB user**: Se crean (o asumen existen) dos roles de PostgreSQL: `plataforma` (admin, usado por el pipeline) y `metabase_readonly` (read-only, usado por Metabase para su DB connection). El setup inicializa este rol si no existe.
- **Metabase API stability**: La API REST de Metabase es relativamente estable (pequeñas diferencias entre versiones en naming); se asume la versión actual de la imagen en `docker-compose.yml`. Si una versión de Metabase rompe la API, la feature deja de funcionar y se debe bumpar/pinear la versión.
- **Governance "Opción A" (confirmed by user during spec clarificación)**: El SQL que llega a las cards de Metabase YA tiene RLS aplicada por el resolver. Metabase ejecuta pero no re-aplica RLS nativa. Confirmado durante la clarificación de la spec — no se necesita replicar la RLS con Group Policies nativas de Metabase.

## Out of Scope

- **Metabase como herramienta de autoría ad-hoc** — no se expone el editor de SQL de Metabase para escribir consultas desde cero. Solo se visualiza lo que el pipeline generó.
- **Metabase Sandboxes / Group Policies nativas** — sería una replicación de la RLS nativa de Metabase. No se implementa; el approach es SQL gobernado (Opción A).
- **Embedding de dashboards** — fuera de scope; se usa la UI nativa de Metabase en `http://localhost:3000`.
- **Auth real (OIDC/JWT) sobre Metabase** — deferred a v3.0+.
- **Multi-tenant Metabase** — fuera de scope.
- **Migración a BigQuery** — sigue fuera de scope.
- **Cambio del Semantic Layer o de los contracts RLS** — este feature CONSUME el `GovernedQueryProvider`, no lo modifica. Las cards SIEMPRE usan el SQL gobernado existente.
- **Modelo fine-tuning, dashboard UI custom** — fuera de scope.
