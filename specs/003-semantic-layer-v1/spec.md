# Feature Specification: Semantic Layer v1 (Governed Metrics, Dimensions & RLS)

**Feature Branch**: `003-semantic-layer-v1`

**Created**: 2026-08-17

**Status**: Draft

**Input**: User description: "Crear la capa semántica (Semantic Layer) del proyecto. No debería ser MUY compleja ya que ya tenemos mucho contexto (data dictionary, data model, semantic source, Text-to-SQL v1 sobre Orders). Va a ser el v2.0 del roadmap original: Semantic Layer + Governance sobre Returns (net vs gross) y People (RLS por región)."

## Scope Summary

Esta especificación define el milestone **v2.0** de la Plataforma de Datos y GenAI: una **Semantic Layer** declarativa y gobernada que se interpone entre el warehouse (PostgreSQL con `Orders`, `Returns`, `People`) y los consumidores (el pipeline de Text-to-SQL de la feature `002-text-to-sql-v1`, futuras APIs/BI).

Es el siguiente hito del roadmap (**M3** en `README_STATUS.md`), y resuelve simultáneamente:

1. **Semántica de negocio formalizada** — métricas, dimensiones y relaciones declaradas en un modelo único y validable (sustituyendo el uso "interino" del `DataDictionaryDocument` como contexto semántico del LLM que se introdujo en 002).
2. **Lógica de negocio sobre `Returns`** — la pieza del roadmap que aparece desde v0: distinguir **gross sales** (SUM de `Orders.Sales`) de **net sales** (gross menos las líneas devueltas mediante `Returns`), además de `return_rate`, `returned_amount`, etc.
3. **Row-Level Security (RLS) por `Region` usando `People`** — la pieza NON-NEGOTIABLE de la constitución (Principle IV): ningún SQL generado por el LLM puede bypassar la resolución de gobernanza. El Semantic Layer rewrite/filtra el SQL antes de ejecutarlo, scoped por el `Region` del `viewer`.

### Entregables concretos (v2.0)

- Un **`SemanticLayerDocument`** (Pydantic v2) con métricas, dimensiones, relaciones (`Orders`-`Returns` por `Order ID`; `Orders`-`People` por `Region`), y la definición de los `Viewer`s soportados.
- Un **builder** que construye el document a partir de las fuentes existentes (`semantic_source.py`, `data_dictionary.md`, `data-model.md`, `load_manifest.json`) — sin duplicar ni inventar semántica nueva.
- Un **resolver** `SemanticQueryResolver` que, dado un SQL validado + un `SemanticViewer`, aplica RLS añadiendo/forzando el filtro `Region IN (viewer.regions)` y devuelve el SQL gobernado.
- **Enforcement en el pipeline de Text-to-SQL**: el `QueryProvider.execute_readonly_query` se ejecuta **siempre** a través del Semantic Layer cuando hay un viewer activo (no hay bypass). El contrato de la feature 002 ya lo anticipaba ("v2.0 will intercept it").
- **Integración con `PromptBuilder`**: el prompt del LLM ahora se enriquece opcionalmente con métricas y dimensiones (no solo columnas), para que distinga net vs gross y sepa qué es agregable.
- **Artifact** versionado (`semantic_layer.md` + `semantic_layer.json`) regenerable vía CLI.
- **Comandos CLI**: `generate-semantic-layer`, `ask` (ahora con RLS aplicado), y `ask --viewer <viewer-id>`.
- **Tests de contrato** para todos los boundaries y **tests de integración** que prueban que el RLS efectivamente filtra filas (un viewer con región N no ve filas de región M).

### Explicitly out of scope

- **RBAC column-level fino** (ocultar columnas individuales por rol) — declarado en el modelo pero NO enforceado en v2.0; solo RLS por fila/scope de `Region` está enforced. Esto cubre el principio constitucional NON-NEGOTIABLE de gobernanza (RLS por fila) sin construir un sistema completo de permisos por columna.
- **Sistema de autenticación real** — no hay login/sesiones/JWT/OIDC. El `SemanticViewer` se construye desde env vars / flag CLI / archivo de configuración local. Es suficiente para demostrar y testear governance sin provisioning de identidad.
- **Audit logging y lineage completos** — la constitución los incluye en el "Semantic Layer from the first feature"; aquí se deja **preparado** (el punto de extensión de logging existe y registra `viewer` y SQL gobernado) pero no se construye un sistema de audit persistente / índice de lineage.
- **Multi-tenant** — un solo tenant; el `viewer` define regiones permitidas, no hay namespaces por tenant.
- **Cambio de Text-to-SQL para soportar `Returns`/`People` como superficie de consulta directa** — el LLM sigue generando SQL sobre `Orders` (con joins a `Returns` cuando pide métricas derivadas como net sales). La feature 002 limitó a `Orders`-only y eso se mantiene; el cambio es que el LLM ahora ve las métricas/relaciones y puede generar SQL que **join** con `Returns` (siendo `Returns` ya parte del alcance para este cálculo derivado). `People` solo se usa para resolver el mapping `viewer → regiones`, nunca como superficie de consulta del LLM.
- **BigQuery migration** — fuera de scope; PostgreSQL local solo.
- **Dashboard/UI, multi-turn conversation, model fine-tuning** — fuera de scope (igual que en 002).

### Roadmap context

| Hito | Estado | Feature |
| --- | --- | --- |
| M0 | Completado | `001-data-genai-platform-baseline` (warehouse + dictionary) |
| M1 | Completado | `002-text-to-sql-v1` v1.0 (pipeline NL→SQL sobre Orders) |
| M2 | Completado | `002-text-to-sql-v1` v1.1 (logging + sanity-check) |
| **M3** | **En curso** | **`003-semantic-layer-v1` (esta feature)** |

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Materializar la capa semántica como artifact declarativo y gobernado (Priority: P1) 🎯 MVP

Un Data Engineer o AI Engineer quiere un único artefacto declarativo y versionable (`semantic_layer.md` + `semantic_layer.json`) que capture: qué tablas son hechos/dimensión, qué métricas existen (con fórmula y descripción de negocio), qué dimensiones hay para desglosar/agregar, y cómo se unen las tablas. Es el contrato que reemplaza el uso interino del `DataDictionaryDocument` como contexto del LLM y da soporte formal a net-vs-gross y cualquier métrica futura.

**Why this priority**: Sin el modelo semántico formal, no hay base para ni RLS ni para que el LLM distinga net de gross. Es el cimiento que desbloquea US2 (RLS), US3 (integración con Text-to-SQL) y US4 (CLI). Es el MVP que entrega valor independiente: aunque no se conectara al pipeline todavía, ya es la fuente de verdad de negocio.

**Independent Test**: Ejecutar `uv run python -m src.cli.main generate-semantic-layer` y verificar que se generan `semantic_layer.md` y `semantic_layer.json` con: las 3 tablas, métricas `gross_sales`, `net_sales`, `returned_amount`, `return_rate` (cada una con fórmula SQL y tabla fuente), dimensiones `region`, `country`, `segment`, `category`, `sub_category`, `order_date`, `ship_mode`, `order_priority`, mercado, customer, product, y las relaciones Orders↔Returns por `Order ID` y Orders↔People por `Region`.

**Acceptance Scenarios**:

1. **Given** el warehouse cargado y el `DataDictionaryDocument` regenerado, **When** se ejecuta `generate-semantic-layer`, **Then** se produce un `SemanticLayerDocument` (Pydantic v2) cubriendo las 3 tablas, con métricas y dimensiones derivadas de `semantic_source.py` y `data_dictionary.md` (sin inventar semántica nueva).
2. **Given** el artifact generado, **When** un reviewer lo lee, **Then** encuentra definida la métrica `net_sales` con fórmula que referencia `Returns` (gross minus returned lines) y una descripción de negocio clara.
3. **Given** el artifact generado, **When** se serializa a JSON y se regenera desde cero en el mismo estado de warehouse, **Then** el JSON resultante es determinista (byte-identical o canonical equivalente — mismo sha256 del `semantic_source.py`).
4. **Given** una columna que no existe en el `DataDictionaryDocument`, **When** el builder intenta crear una métrica/dimensión que la referencia, **Then** falla rápido con un error claro (validación en build-time).

---

### User Story 2 — Enforzar RLS: ningún SQL del LLM bypassa gobernanza (Priority: P1)

Un Data Engineer o stakeholder necesita garantía constitucional (Principle IV, NON-NEGOTIABLE) de que el SQL generado por Text-to-SQL nunca exponga filas fuera del scope del `viewer`. El Semantic Layer intercepta el SQL validado y aplica un filtro por `Region` antes de ejecutarlo, usando el mapping `People` (persona → regiones que gobierna).

**Why this priority**: Es el requisito NON-NEGOTIABLE de la constitución y estaba explícitamente diferido a v2.0 desde la feature 002 ("v2.0 will intercept it"). Es P1 porque sin esto el sistema **no puede reclamar** tener gobernanza (la constitution lo prohibe: "No feature without a functioning Semantic Layer may claim to provide governance").

**Independent Test**: Crear dos `viewer`s con regiones distintas (e.g., `alice` con regiones `[Caribbean]` y `bob` con `[Central US]`); correr `ask "total sales"` con cada viewer; verificar que los totales difieren y coinciden con `SELECT SUM(Sales) FROM orders WHERE Region = '<region>'` ejecutado directamente sobre el warehouse.

**Acceptance Scenarios**:

1. **Given** el Semantic Layer cargado y un `SemanticViewer{regions: [R1, R2]}`, **When** un SQL validado `SELECT ... FROM orders WHERE ...` (o sin WHERE) pasa por el resolver, **Then** el SQL ejecutado contiene forzosamente `Region IN ('R1','R2')` AND-ed con cualquier `WHERE` existente.
2. **Given** un SQL que ya tiene un `WHERE Region = ...`, **When** se aplica RLS, **Then** el resolver preserva el filtro del usuario Y le AND-ea el scope del viewer (no lo sobreescribe, lo refina) — un viewer sin regiones `R1` no puede ver `R1` aunque su SQL lo pida.
3. **Given** un `viewer` con `regions: []` (vacío), **When** se procesa cualquier SQL, **Then** el resultado es 0 filas (el viewer no ve nada) — el resolver aplica un predicado `FALSE` o equivalente.
4. **Given** el `QueryProvider` con un viewer activo, **When** se llama `execute_readonly_query(sql, table_def)`, **Then** NO existe ningún path en el código que ejecute el SQL *sin* pasar por el Semantic Layer (boundary test lo garantiza).
5. **Given** Text-to-SQL con un viewer configured (env var o `--viewer`), **When** se hace `ask "total sales"`, **Then** el SQL ejecutado está scoped por las regiones del viewer y los resultados reflejan solo sus regiones.

---

### User Story 3 — Enriquecer el prompt de Text-to-SQL con capa semántica (Priority: P2)

Un AI Engineer quiere que el LLM de Text-to-SQL distinga `gross_sales` de `net_sales`, conozca las dimensiones agregables y los joins válidos, en lugar de tener solo el esquema crudo. El `PromptBuilder` ahora puede incluir opcionalmente el `SemanticLayerDocument` (métricas con descripción de negocio, dimensiones, relaciones, joins válidos) en el prompt.

**Why this priority**: P2 porque Text-to-SQL ya funciona (feature 002) con el contexto interino del `DataDictionaryDocument`. Esta story es mejora de calidad (precisión de métricas) y se construye encima de US1. No bloquea US2 ni el MVP — entrega valor incremental.

**Independent Test**: Hacer `ask "show me net sales by region"` con semantic layer en contexto y verificar que el SQL generado nunca hace `SUM(Sales)` simple cuando se pide `net`; debería hacer un LEFT JOIN con `Returns` o aplicar la lógica de `returned_amount` provista por la capa semántica.

**Acceptance Scenarios**:

1. **Given** `SemanticLayerDocument` generado, **When** el `PromptBuilder` construye un prompt, **Then** el prompt incluye las métricas definidas con su fórmula y descripción de negocio (en formato condensado).
2. **Given** el prompt enriquecido, **When** el LLM recibe "net sales by region", **Then** el SQL generado referencia `Returns` (vía join o subconsulta) y no devuelve el mismo número que `gross sales`.
3. **Given** Text-to-SQL sin semantic layer disponible (e.g., `--no-semantic-layer` flag), **When** se hace `ask`, **Then** se vuelve al comportamiento de la feature 002 (`DataDictionaryDocument` como contexto) — fallback explícito, sin romper.
4. **Given** el `PromptBuilder` con semantic layer, **When** se serializa para el prompt, **Then** el bloque semántico added mantiene el tamaño del prompt dentro de un bound razonable (~+300-500 tokens sobre el prompt de 002).

---

### User Story 4 — Operar la Semantic Layer por CLI (Priority: P2)

Un Data Engineer o AI Engineer quiere generar/inspeccionar el artifact semántico y seleccionar un `viewer` desde la CLI, sin tocar código.

**Why this priority**: P2 — se construye sobre US1 (generar artifact) y US2 (RLS). Es la capa operativa que hace las stories anteriores utilizables. No es MVP por sí sola (no entrega valor sin US1), pero es necesaria para que el workflow end-to-end sea reproducible.

**Independent Test**: Correr `generate-semantic-layer` y luego `ask --viewer alice "total sales"`; verificar el artifact generado y que el cambio de viewer altera los resultados.

**Acceptance Scenarios**:

1. **Given** el warehouse cargado, **When** se ejecuta `generate-semantic-layer`, **Then** se escriben `semantic_layer.md` y `semantic_layer.json` en `.artifacts/` y se imprime un resumen (métricas, dimensiones, relaciones, viewers definidos).
2. **Given** viewers definidos en `.env` o un archivo de configuración (e.g., `SEMANTIC_VIEWERS_FILE`), **When** se ejecuta `ask --viewer <id> "<question>"`, **Then** el SQL ejecutado está scoped por las regiones del viewer y los resultados reflejan solo sus regiones.
3. **Given** `ask` sin `--viewer`, **When** se ejecuta, **Then** falla rápido con un error claro indicando que se requiere un viewer (governance no es opcional — constitución Principle IV), a menos que se pase --allow-full-access (solo admitido en ambiente local/dev, y el log lo captura como evento de gobernanza).
4. **Given** un viewer inexistente, **When** se pasa `--viewer <unknown>`, **Then** falla rápido con un error claro listando los viewers disponibles.

---

### Edge Cases

- **¿Qué pasa cuando el SQL generado por el LLM ya tiene un `WHERE Region = ...` para una región fuera del scope del viewer?** El resolver preserva y AND-ea el scope del viewer — el resultado está forzosamente dentro de la intersección. El viewer no puede ver regiones fuera de su scope por más que el SQL lo pida.
- **¿Qué pasa cuando el viewer tiene `regions: []`?** El resolver aplica `FALSE` y devuelve 0 filas para cualquier consulta. Se loguea como evento de gobernanza.
- **¿Qué pasa cuando el mapping `People`-`Region` tiene el mismatch documentado (People divide `Canada` en Eastern/Western, Orders tiene `Canada` solo)?** El resolver usa el mapping tal cual está, y la correspondencia best-effort; si el viewer está scoped a `Eastern Canada`, la query efectiva filtra `Region IN ('Eastern Canada')` y devuelve 0 filas de Orders (no match) — el mismatch se documenta y se prefiere que falle a que filtre con postData. La resolución de la taxonomía es v3.0+.
- **¿Qué pasa cuando una métrica referenciada en el prompt no existe en el Semantic Layer?** El LLM puede generar SQL con la fórmula que mejor entienda; el resolver solo enforce RLS, no valida que el SQL "use" la métrica. Eso es responsabilidad del LLM. Si el SQL generá columnas inexistentes, el `SqlValidator` (de 002) ya lo rechaza.
- **¿Qué pasa cuando `Returns` tiene duplicados por `Order ID` (multi-line returns)?** El `SemanticLayerDocument` documenta esto en la definición de `net_sales` (la fórmula usa un LEFT JOIN condicional que descuenta una vez por línea retornada, no por retorno). Queda explicitado en el artifact.
- **¿Qué pasa cuando el warehouse no está corriendo?** `generate-semantic-layer` puede construir el artifact desde las fuentes estáticas (`semantic_source.py` + `data_dictionary.md`) **sin** necesidad de DB viva; `ask` y RLS sí requieren el DB.
- **¿Qué pasa cuando `FORGE_API_KEY` falta?** Falla rápido como en 002 (FR-013).

## Requirements *(mandatory)*

### Functional Requirements

#### Semantic Layer Document (US1)

- **FR-001**: El sistema MUST producir un `SemanticLayerDocument` (Pydantic v2, frozen) que cubre las 3 tablas (`Orders`, `Returns`, `People`), con: clasificación hecho/dimensión por tabla, métricas, dimensiones, relaciones, y viewers declarados.
- **FR-002**: Para cada métrica, el document MUST incluir: `name` (snake_case), `business_description` (lenguaje de negocio), `formula_sql` (expresión SQL válida referenciando columnas existentes), `source_table` (tabla origen), `derives_from` (si la métrica es derivada, e.g., `net_sales` deriva de `gross_sales` + `Returns`), y `aggregation` (SUM/AVG/COUNT/etc.).
- **FR-003**: El document MUST definir al menos las métricas: `gross_sales` (SUM(Orders.Sales)), `net_sales` (gross minus returned lines via Returns), `returned_amount` (SUM de Sales de líneas retornadas), `return_rate` (returned_amount / gross_sales), `total_profit` (SUM(Orders.Profit)), `net_profit` (total_profit minus returned_amount), `avg_order_value` (gross_sales / COUNT(DISTINCT Order ID)), `order_count` (COUNT(DISTINCT Order ID)).
- **FR-004**: Para cada dimensión, el document MUST incluir: `name`, `column`, `source_table`, `business_description`, `aggregation_type` (categorical/time/etc.).
- **FR-005**: El document MUST declarar las relaciones: `Orders.Order ID` ↔ `Returns.Order ID` (N:1 desde Returns), `Orders.Region` ↔ `People.Region` (N:1 desde Orders), con `join_type` (LEFT/INNER) y `cardinality`.
- **FR-006**: El builder MUST construir el document desde las fuentes existentes (`semantic_source.py`, `DataDictionaryDocument`, `data-model.md`) — no inventa ni duplica semántica. Si una columna referenciada no existe en el `DataDictionaryDocument`, falla en build-time.
- **FR-007**: El system MUST serializar el document a `semantic_layer.md` (human-readable) y `semantic_layer.json` (canonical JSON, byte-determinista para el mismo estado de inputs).
- **FR-008**: El document MUST ser **regeneratable** vía CLI desde el warehouse cargado o desde las fuentes estáticas (sin DB viva), para que se mantenga en sync.

#### RLS Enforcement (US2)

- **FR-009**: El sistema MUST definir un `SemanticViewer` (Pydantic v2): `viewer_id: str`, `regions: list[str]` (vacío permitido), `allows_full_access: bool = False` (con advertencia de gobernanza al loguear).
- **FR-010**: El sistema MUST implementar un `SemanticQueryResolver` con método `apply_rls(sql: str, viewer: SemanticViewer, table_def: TableDef) -> str` que devuelve el SQL con el filtro `Region IN (:regions)` AND-eado con cualquier `WHERE` existente.
- **FR-011**: El `SemanticQueryResolver` MUST ser puro (no ejecuta SQL, no llama LLM, no DB) y unit-testeable. El SQL transformado es válido PostgreSQL.
- **FR-012**: El `QueryProvider.execute_readonly_query` MUST pasar por el `SemanticQueryResolver` cuando hay un `viewer` activo. No existe path alternativo sin gobernanza. El boundary test lo fuerza.
- **FR-013**: Cuando `viewer.allows_full_access = True`, el resolver NO filtra, PERO el pipeline MUST loguear un evento de gobernanza (`gov.bypass` con `viewer_id` y `sql`) para auditoría. Este flag solo se admite en ambientes locales/dev (check por env var `ENV=local` o `dev`).
- **FR-014**: Cuando `viewer.regions = []` y `allows_full_access=False`, el resolver devuelve SQL que resulta en 0 filas (predicado `FALSE` o equivalente), para que ningún viewer "sin regiones" vea fila alguna.

#### Integration con Text-to-SQL (US3)

- **FR-015**: El `PromptBuilder` MUST aceptar opcionalmente un `SemanticLayerDocument` y, cuando esté presente, enriquecer el prompt con: las métricas (name + business_description + formula_sql condensada), las dimensiones, y las relaciones/joins válidos.
- **FR-016**: El `PromptBuilder` MUST maintain compatibilidad con la feature 002: cuando no hay semantic layer, usa `DataDictionaryDocument` como antes (fallback explícito, sin romper).
- **FR-017**: El prompt con semantic layer MUST mantenerse dentro de un bound razonable (~+300-500 tokens sobre el prompt de 002). Las fórmulas SQL se condensan.

#### CLI (US4)

- **FR-018**: El sistema MUST proveer `generate-semantic-layer` que escribe `semantic_layer.md` + `semantic_layer.json` en `.artifacts/` e imprime un resumen (tables, metrics, dimensions, relationships, viewers).
- **FR-019**: El comando `ask` MUST aceptar `--viewer <id>` (un viewer declarado en config). Sin viewer, falla rápido salvo `--allow-full-access` solo en ambiente local/dev.
- **FR-020**: El sistema MUST cargar viewers desde un archivo de configuración (`SEMANTIC_VIEWERS_FILE` o `.env`), no de código. Falla rápido si el viewer solicitado no existe, listando los disponibles.
- **FR-021**: `ask` con `--viewer` MUST loguear cada call con `viewer_id`, `regions` y `sql_governed` en `.artifacts/text_to_sql.log` (extiende el logging de 002 FR-014 con gobernanza).

#### Arquitectura

- **FR-022**: Los contract models del Semantic Layer MUST vivir en `src/contracts/semantic_layer.py` (Pydantic v2, frozen, typed-boundaries per constitution Principle I).
- **FR-023**: El builder, resolver y registry del Semantic Layer MUST vivir en `src/data_engineering/semantic_layer/` (subpaquete nuevo bajo Data Engineering — la semántica es responsabilidad de Data Engineering per constitution Principle II).
- **FR-024**: La integración con Text-to-SQL (enriquecer prompt, interceptar SQL con RLS) vive en `src/ai_engineering/` (modificando `prompt_builder.py`, `pipeline.py`) PERO las capacidades semánticas se importan a través de `src/contracts/` y la `Protocol`s declaradas — no se importa `src/data_engineering/semantic_layer/` directamente desde `src/ai_engineering/` (Principle II/III respected).
- **FR-025**: El boundary test en `tests/contract/test_boundaries.py` MUST ser extendido para verificar: (a) `openai`/`httpx` solo en `ai_engineering`, (b) `psycopg` solo en `data_access/adapters/postgres`, (c) **nuevo**: la lógica de RLS `apply_rls` solo se invoca desde el adapter/camino del `QueryProvider` — el Semantic Layer no importa `psycopg` ni `openai`.

### Key Entities

- **`SemanticLayerDocument`**: El artifact top-level. Tablas (clasificadas hecho/dimensión), métricas, dimensiones, relaciones, `Viewer`s declarados.
- **`Metric`**: `name`, `business_description`, `formula_sql`, `source_table`, `aggregation` (SUM/AVG/COUNT/etc.), `derives_from: list[str] | None` (métricas que dependen de otras o de `Returns`).
- **`Dimension`**: `name`, `column`, `source_table`, `business_description`, `dimension_type` (`categorical`/`temporal`/`geographic`).
- **`SemanticRelationship`**: `from_table`, `from_column`, `to_table`, `to_column`, `cardinality` (1:N, N:1, 1:1), `join_type` (LEFT/INNER).
- **`SemanticViewer`**: `viewer_id`, `regions: list[str]`, `allows_full_access: bool = False`, `is_local_dev: bool = False`.
- **`SemanticQueryResolver`** (interface, no modelo): `apply_rls(sql, viewer, table_def) -> str`. Puro, no ejecuta ni llama LLM.
- **`SemanticLayer`** (Protocol): `get_document() -> SemanticLayerDocument`, `resolve_query(sql, viewer, table_def) -> str`. Es el boundary que el resto del código puede importar.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un contributor puede ejecutar `generate-semantic-layer` desde un clean clone (tras `bootstrap`) y se producen `semantic_layer.md` y `semantic_layer.json` con las N=8 métricas, M>=8 dimensiones, 2 relaciones y P>=2 viewers declarados — en menos de 5 segundos (sin DB viva obligatoria).
- **SC-002** (governance NON-NEGOTIABLE): Para cualquier `viewer` con un set acotado de regiones `[R]`, el resultado de `ask "total sales"` es **exactamente igual** al de `SELECT SUM(Sales) FROM orders WHERE Region IN (R)`. Cero filas fuera de scope. Verificable con test de integración que compara.
- **SC-003**: Un viewer con `regions: []` recibe 0 filas para cualquier consulta — 100% de las calls, sin excepciones path-skipping. Boundary test cubre todos los callers de `execute_readonly_query`.
- **SC-004**: El `PromptBuilder` con semantic layer produce un prompt que el LLM interpreta para generar SQL distinto para "net sales" vs "gross sales" (validable con la sanity-check evaluation de 002 extendida o con un test dirigido).
- **SC-005**: El artifact `semantic_layer.json` es **determinista** — dos regeneraciones consecutivas en el mismo estado de inputs producen el mismo `sha256` (sin timestamps en el JSON canonical).
- **SC-006**: `ask --viewer <id> "<question>"` funciona en un comando desde clean clone (tras `bootstrap` + `generate-semantic-layer` + env vars de viewers) — reproducible, igual que el quickstart de 001/002.
- **SC-007**: `mypy --strict` pasa con cero errores en todo el código nuevo; `openai` stays confined to `ai_engineering`; `psycopg` stays confined to `data_access/adapters/postgres`; `apply_rls` no se llama fuera de su camino gobernado.
- **SC-008**: Cada boundary nuevo (`contracts/semantic_layer.py`, `data_engineering/semantic_layer/`, integración con `ai_engineering`) tiene contract test; RLS tiene integration test contra PostgreSQL real en Docker.

## Assumptions

- **Prerequisitos**: Features `001` (baseline) y `002` (Text-to-SQL v1) completas y mergeadas. El warehouse está cargado; `DataDictionaryDocument` está disponible; `ask` y `evaluate` funcionan.
- **Semantic source is authoritative**: El `semantic_source.py` curado en la feature 001 (descripciones Kaggle) es la fuente de verdad de negocio. El Semantic Layer **no inventa** semántica nueva; solo la estructura formalmente (métricas, dimensiones, relaciones). Nuevas descripciones se agregan en `semantic_source.py`, no en el Semantic Layer.
- **`Returns` mismatch on `Order ID`**: Ya documentado en el `data_dictionary.md` — `Returns.Order ID` tiene 63 duplicados (multi-line returns) y se introdujo `Return ID` como PK surrogate. La fórmula de `net_sales` lo respeta: descuenta por **línea retornada**, no por orden retornado. Esto se documentará claramente en el artifact.
- **`Region` taxonomy mismatch**: Documentado en el `data_dictionary.md`: People divide `Canada` en Eastern/Western; Orders tiene solo `Canada`. Para v2.0 se hace matching best-effort y el resolver es conservador (si el viewer pide `Eastern Canada` y Orders solo tiene `Canada`, no match → 0 filas). La consolidación de taxonomía es v3.0+ (out of scope aquí).
- **Viewers file**: Configuración local via `SEMANTIC_VIEWERS_FILE` (default `.env` o un YAML/JSON `viewers.json`). Format exacto se decide en `plan.md`. En dev local, los viewers se pueden definir en `.env`.
- **`allows_full_access`**: Es un escape exclusivamente local/dev (check `ENV in {local, dev, test}`). En staging/prod el flag se ignora y se fuerza RLS siempre. Logueado como `gov.bypass`.
- **Governance scope**: RBAC **column-level** (ocultar columnas por rol) NO está enforced — solo RLS por fila/scope de `Region`. Esto es consistente con la constitución: RLS está en Semantic Layer, RBAC está declarado pero no enforcement completo en v2.0. Queda para v3.0+.
- **Audit/lineage**: El logging de governance (viewer, SQL gobernado) se implementa, pero no se construye un sistema de audit persistente / índice de lineage. Solo el `.artifacts/text_to_sql.log` extendido.
- **Text-to-SQL surface**: El LLM sigue generando SQL contra `Orders` con **joins** a `Returns` para métricas derivadas. `People` NO es superficie de consulta del LLM — solo alimenta el mapping `viewer → regions`.
- **Local only**: PostgreSQL local en Docker. BigQuery fuera de scope (migración futuro). Sin multi-tenant, sin auth real.
- **Determinism**: `semantic_layer.json` es canonical/determinista para un estado dado de inputs (sin timestamps en el JSON; el `generated_at` se omite del JSON, solo va en el `.md`).

## Out of Scope

- **RBAC column-level enforcement** — declarado en el modelo pero no enforceado en v2.0 (RLS por fila sí está enforced).
- **Sistema de autenticación real (login/sesiones/OIDC/JWT)** — fuera de scope; `SemanticViewer` se construye desde config local.
- **Audit logging persistente y lineage indexable** — logging básico sí, sistema completo no.
- **Multi-tenant / multi-org**.
- **Resolución del mismatch de taxonomía `People.Region` vs `Orders.Region`** — v3.0+.
- **Migración a BigQuery** — fuera de scope, PostgreSQL local.
- **Dashboard/UI, conversación multi-turn, model fine-tuning** — igual que en 002.
- **Spec new metrics on-the-fly desde CLI** — las métricas se definen estáticamente en el Semantic Layer (built from `semantic_source.py`); no hay DSL runtime de métricas por usuario.
