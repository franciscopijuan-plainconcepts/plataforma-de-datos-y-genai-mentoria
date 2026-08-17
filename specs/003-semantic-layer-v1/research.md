# Research: Semantic Layer v1 (Governed Metrics, Dimensions & RLS)

**Phase**: 0 (Outline & Research)
**Feature**: 003-semantic-layer-v1
**Date**: 2026-08-17
**Status**: Complete — todos los NEEDS CLARIFICATION resueltos

> Este documento resuelve las 5 clarificaciones abiertas en `plan.md` Technical Context:
> 1. Estrategia de aplicación de RLS al SQL (reescribir vs. envolver)
> 2. Formato del archivo de viewers
> 3. Integración del SemanticLayerDocument en el PromptBuilder sin explotar tokens
> 4. Determinismo de `semantic_layer.json`
> 5. Boundary enforcement: cómo garantizar que ningún path bypassa RLS
>
> Y agrega Part F (datos clave del data dictionary que la implementación debe respetar).

---

## Part A — RLS Strategy: Predicate Injection (no subquery wrapping)

### Decision: Inyectar `WHERE "Region" IN (...)` directamente dentro del SQL del LLM

**Decision (implementación final, post-corrección de la versión inicial de subquery wrapping)**: El
`SemanticQueryResolver.apply_rls(sql, viewer, table_def)` **inyecta** el predicado
`"Region" IN ('R1', 'R2', ...)` directamente en el SQL generado por el LLM,
AND-ando el `WHERE` existente o introduciendo un `WHERE` nuevo antes de
`GROUP BY` / `ORDER BY` / `LIMIT` / fin-de-query. El `_wrap_false` path (viewer
con `regions: []`) sigue usando subquery wrapping con `WHERE FALSE` por
robustez.

Donde `<original_sql>` es el SQL generado por el LLM y ya validado por el
`SqlValidator` (que garantiza: SELECT-only, single-statement, no comments, no
forbidden keywords, Orders+Returns-only-table-naming, existing-columns-only).

**Rationale**:

- **Bug reportado en la versión inicial (subquery wrapping)**: la versión
  inicial hacía `SELECT * FROM (<sql>) AS _gov WHERE "Region" IN (...)`.
  Esto fallaba para aggregaciones como `SELECT SUM("Sales") FROM Orders` — el
  inner SELECT no expone la columna `Region` en su proyección outer, así que
  el `WHERE "Region" IN (...)` del wrapper exterior no la encontraba y lanza
  `column "Region" does not exist`. La inyección directa resuelve esto porque
  el predicado se aplica sobre la tabla `Orders` directamente en el inner query.
- **Robusto frente a WHERE existente** — si el LLM genera
  `SELECT ... FROM Orders WHERE "Region" = 'X'`, la inyección resulta en
  `SELECT ... FROM Orders WHERE "Region" = 'X' AND "Region" IN ('R1', 'R2')`
  — la intersección natural (el viewer no ve regiones fuera de scope aunque
  el SQL las pida). Alineado con acceptance scenario 2 del US2.
- **Robusto frente a GROUP BY / ORDER BY / LIMIT** — la inyección del predicado
  se inserta **antes** del primer `GROUP BY` / `ORDER BY` / `HAVING` / `LIMIT`
  / `OFFSET` que se encuentre después del WHERE, así que el orden de cláusulas
  queda válido SQL. Tested en `test_semantic_resolver.py`.
- **Predicado pushdown** — al estar el predicado en el inner query, PostgreSQL
  lo empuja automáticamente al scan de `Orders`, eficiente sin plan extra.
- **Case-sensitivity**: PostgreSQL dobla los identifiers no-comillados a
  lowercase; los identifiers del dataset son title-case (`"Region"`). El
  resolver SIEMPRE usa `"Region"` quoted con double-quotes para matchear la
  convención del dataset y del `SqlValidator` existente.
- **Consistencia con el `SqlValidator`**: la feature 002 ya valida single-
  statement + SELECT-only + Orders-only (+ Returns JOIN desde v2.0). El
  resolver confía en esa garantía previa para que el regex de inserción del
  predicado sea seguro (la estructura del SQL es predecible).
- **Sobrevive íntegro dentro de EXISTS subqueries**: si el SQL del LLM tiene un
  subquery con `EXISTS (SELECT 1 FROM Returns ...)`, el predicado se inserta
  en el WHERE outer — el subquery EXISTS queda intacto. Test cubre este caso.

### Alternatives consideradas

- **Regex para insertar `Region IN (...)` en el `WHERE` existente**: muy frágil (¿dónde está el `WHERE`? ¿Y si hay `HAVING`? ¿Y si el `WHERE` está dentro de un subquery?). Rejectado.
- **`sqlglot` (parser SQL)** — añade una dependencia nueva y complejidad de parsing para un problema que el wrapping resuelve elegantly. Rejectado por YAGNI (mismo argumento que en 002 para el `SqlValidator`).
- **Crear vistas SQL materializadas con RLS_POLICY** — rompería la idempotencia del `bootstrap` de la baseline (tendría que haber migraciones), acoplaría governance a DDL y complicaría la migración a BigQuery. Rejectado — governance en runtime layer, no en DDL.

### Casos edge

| Input | Output esperado |
|---|---|
| `SELECT * FROM Orders` (viewer: `[R1]`) | `SELECT * FROM Orders WHERE "Region" IN ('R1')` |
| `SELECT SUM("Sales") FROM Orders` (viewer: `[R1, R2]`) | `SELECT SUM("Sales") FROM Orders WHERE "Region" IN ('R1', 'R2')` ← funciona con inyección directa (no wrap) |
| `SELECT "Region", SUM("Sales") FROM Orders GROUP BY "Region"` (viewer: `[R1]`) | `SELECT "Region", SUM("Sales") FROM Orders WHERE "Region" IN ('R1') GROUP BY "Region"` |
| `SELECT * FROM Orders WHERE "Region" = 'R3'` (viewer: `[R1, R2]`) | `SELECT * FROM Orders WHERE "Region" = 'R3' AND "Region" IN ('R1', 'R2')` (intersección vacía → 0 filas; el viewer no ve R3) |
| `SELECT * FROM Orders ORDER BY "Sales" LIMIT 10` (viewer: `[R1]`) | `SELECT * FROM Orders WHERE "Region" IN ('R1') ORDER BY "Sales" LIMIT 10` (predicado antes del ORDER BY) |
| Viewer con `regions: []` y `allows_full_access=False` | `SELECT * FROM (<sql>) AS _gov WHERE FALSE` (devuelve 0 filas, PostgreSQL válido; subquery wrap aquí sí es OK porque no hay proyección externa que dependa de "Region") |
| Viewer con `allows_full_access=True` (solo en ENV local/dev) | Devuelve el SQL original sin inyección + loguea `gov.bypass` |
| SQL con `;` final | El `SqlValidator` ya lo habría dejado pasar; el resolver lo stripea antes de inyectar |
| SQL con `EXISTS (SELECT 1 FROM Returns WHERE ...)` | El predicado `Region IN` se inserta en el WHERE outer; el EXISTS subquery queda intacto dentro del body del SQL |

### Implementación (esqueleto)

```python
def apply_rls(sql: str, viewer: SemanticViewer, table_def: TableDef) -> str:
    if viewer.allows_full_access:
        log_governance_bypass(viewer, sql)
        return sql
    if not viewer.regions:
        return f'SELECT * FROM ({sql}) AS _gov WHERE FALSE'
    regions_quoted = ", ".join(f"'{r.replace(chr(39), chr(39)*2)}'" for r in viewer.regions)
    return f'SELECT * FROM ({sql}) AS _gov WHERE "Region" IN ({regions_quoted})'
```

Notas:
- El `;` final (si lo hubiera) lo quita el `SqlValidator` upstream; aquí no se maneja.
- El escaping de comillas en `regions` es defensive — el `viewer` se carga de un archivo local controlado (no user input del LLM), pero igual sanitizamos.
- `_gov` como alias externo — nunca chocará con alias de usuario (case-sensitive en PG underside).
- El resolver es una **pure function** (no DB, no LLM, no side effects besides logging de governance bypass) — unit-testeable exhaustivamente.

---

## Part B — Prompt Integration con SemanticLayerDocument

### Decision: Condensación selectiva de métricas + dimensiones + joins (~+400 tokens)

**Decision**: Cuando el `PromptBuilder` recibe un `SemanticLayerDocument` (además del `DataDictionaryDocument`), añade un bloque nuevo al prompt con este formato:

```text
Semantic Layer (business metrics available):
- gross_sales = SUM("Sales") over Orders. [Gross sales revenue before returns.]
- net_sales = gross_sales minus returned lines via Returns. [Net sales after returns.]
- returned_amount = SUM("Sales") of line items with a matching Returns entry. [Total returned revenue.]
- return_rate = returned_amount / gross_sales. [Fraction of sales returned.]
- total_profit = SUM("Profit") over Orders. [Total profit.]
- net_profit = total_profit minus returned_amount. [Net profit after returns.]
- avg_order_value = gross_sales / COUNT(DISTINCT "Order ID"). [Average order value.]
- order_count = COUNT(DISTINCT "Order ID"). [Number of distinct orders.]

Dimensions available for GROUP BY / filtering:
- region, country, market (geographic)
- segment (customer segment)
- category, sub_category (product)
- ship_mode, order_priority (operational)
- order_date (temporal — use the column directly)

Joins available when a metric needs them:
- Returns: Orders."Order ID" = Returns."Order ID" (LEFT JOIN to find returned lines).
  Note: Returns has duplicate "Order ID" values (multi-line returns) — each returned
  line is a separate return record.
```

**Tamaño**: ~400 tokens añadidos sobre el prompt condensado de 002 (~500-800 tokens) = total ~900-1200 tokens. Razonable y dentro del bound de SC-004 (~+300-500 tokens).

### Rationale

- **Condensación por propósito**: el LLM no necesita el `formula_sql` completo de cada métrica (ya está en condensación verbal); necesita la **intención** (gross vs net) y el **join a usar** (Returns por Order ID). El bloque explicita ambos.
- **Dimensiones agrupadas por tipo**: facilita que el LLM sepa qué usar paraGROUP BY sin tener que mirar el esquema crudo.
- **Notas de data-quality relevantes**: el mismatch de `Returns.Order ID` (multi-line returns, 63 duplicados) se menciona inline porque afecta directamente cómo construir el JOIN para `net_sales`. Es semilla crítica.
- **Sobre el out-of-scope**:_NO se menciona `People` en el bloque — el LLM no debe saber nada de People porque `People` no es superficie de consulta (solo alimenta el mapping viewer→regions interno del resolver).

### Integration en `build_prompt`

Nueva firma (restrospectivamente compatible con 002):

```python
def build_prompt(
    question: NLQuestion,
    dictionary: DataDictionaryDocument,
    table_def: TableDef,
    semantic_layer: SemanticLayerDocument | None = None,  # NEW optional
) -> str:
    ...
```

- Si `semantic_layer is None`: behaviour idéntico a 002 (fallback explícito, FR-016).
- Si `semantic_layer is not None`: inserta el bloque semántico entre Relationships y Rules.

### Alternatives consideradas

- **Full `semantic_layer.json` dump**: ~5-8k tokens, overkill. Rechazado.
- **Instrucciones de system message separadas**: el SDK soporta multi-mensaje pero añadiría complejidad sin ganar claridad. Rechazado.
- **Few-shot examples de net_sales vs gross_sales**: podría mejorar precisión pero aumenta tokens y requiere curación. Deferred a v3.0 si la evaluación muestra problemas.

---

## Part C — Viewer Resolution: "Login as Person" (default) + YAML fallback

### Decision: La tabla People es el source of truth para personas reales; `viewers.yaml` queda como fallback para escape hatches

**Decision (implementación final, post-mejora del modelo inicial YAML-only)**: el CLI
`ask --viewer <value>` resuelve primero una **persona real** desde la tabla `People`
(el mapping governance canónico por constitution Principle IV) usando
`PeopleViewerResolver`, y solo cae al `viewers.yaml` si el valor no matchea
una persona. Esto elimina la necesidad de mantener duplicado el mapping
`person → region` a mano en YAML.

#### Resolución de viewer (orden de prioridad)

1. **`PeopleViewerResolver` (default, login-as-person)** — consulta la tabla
   `People` (cacheada en memoria por proceso) y resuelve un `SemanticViewer`
   con `viewer_id` derivado del nombre de la persona (normalized a
   snake_case) y `regions = [<region_de_People>]`. Accepta tres formas de
   lookup para maximizar la naturalidad del "login":
   - snake_case normalized ID: `marilene_rousseau`
   - Nombre completo con acentos: `Marilène Rousseau`
   - Nombre sin acentos: `Marilene Rousseau`
2. **`ViewerRegistry` (fallback `viewers.yaml`)** — si `--viewer <value>` no
   matchea ninguna persona en People, el CLI busca `<value>` en
   `viewers.yaml`. Esto preserva los escape hatches (e.g., `admin_dev` para
   bypass en local/dev) y los viewers basados en rol (e.g., `sales_eu`) que
   no correspondan a una persona específica.
3. **Fail-fast** — si ninguno de los dos matchea, el CLI lanza un error
   claro listando los IDs disponibles en People, y sugiere editar el
   `viewers.yaml` para casos custom.

#### Ejemplo de flujo

```bash
uv run python -m src.cli.main ask --viewer marilene_rousseau 'total sales'
# → PeopleViewerResolver lee People, encuentra `Marilène Rousseau -> Caribbean`,
#   construye SemanticViewer(viewer_id='marilene_rousseau', regions=['Caribbean'])

uv run python -m src.cli.main ask --viewer 'Marilène Rousseau' 'total sales'
# → misma persona, lookup por nombre completo con acento

uv run python -m src.cli.main ask --viewer admin_dev 'total sales'
# → PeopleViewerResolver no matchea → cae a ViewerRegistry → encuentra en YAML
#   como allows_full_access=true (solo efectivo si ENV in {local, dev, test})
```

#### Carga inicial de People

`PeopleViewerResolver._load_cache()` hace una única query contra `People`:

```python
SELECT "Person", "Region" FROM "People"
```

usando el `PostgresRepository` *ungoverned* (sin `GovernedQueryProvider` en
medio, porque `People` ES el mapping y no tiene sentido scoping por regiones).
El resultado se cachea en memoria para todo el lifetime del proceso — así
`evaluate` corriendo ~10 preguntas no re-query la tabla 10 veces.

#### Rationale

- **Single source of truth**: `People` *es* el mapping `person → region`.
  Duplicarlo en `viewers.yaml` es aguardar problemas cuando una persona
  cambia de región o se agrega una nueva.
- **Login natural**: el usuario se identifica por su nombre real
  (`marilene_rousseau`) en vez de un ID artificial (`alice`).
- **Sin mantenimiento**: cuando una persona se cambia de región en el
  dataset, el modelo automáticamente usará la nueva región — no hay que
  actualizar un file YAML local.
- **Backward compatible**: el modelo YAML original sigue disponible como
  fallback para casos que no resuelven a una persona (escape hatches, CI
  service accounts, roles).

### Formato `viewers.yaml` (cuando se usa como fallback)

```yaml
# viewers.yaml — local, gitignored. Solo necesario para escape hatches
# (no para personas reales — esas se resuelven via People table).
viewers:
  - id: admin_dev
    regions: []
    allows_full_access: true     # Solo efectivo cuando ENV in {local, dev, test}
  - id: sales_eu
    regions:
      - Western Europe
      - Eastern Europe
      - Northern Europe
      - Southern Europe
    allows_full_access: false
  - id: ci_account
    regions: []
    allows_full_access: false    # Sin acceso a ninguna región (CI smoke test)
```

Cargado por `src/data_engineering/semantic_layer/registry.py` con `pyyaml`.
El path se resuelve:

1. `SEMANTIC_VIEWERS_FILE` env var (si está seteada → usar ese path).
2. Default: `viewers.yaml` en el cwd (raíz del proyecto).
3. Si no existe → el CLI falla rápido con un error claro listando cómo crearlo
   (apunta al `viewers.example.yaml`).

### Rationale histórico (por quéOriginalmente se seleccionó YAML)

- **YAML sobre JSON**: YAML permite comentarios (`#`), mejor lectura para config
  humana, y multiline strings limpios.
- **YAML sobre `.env`**: `.env` es ok para pares k=v pero no escala a listas de
  regions por viewer. YAML estructura nativamente.
- **`pyyaml` es estándar**: ampliamente usado, mantenida, zero-surprise.
- **`.example.yaml` committed**: el `viewers.example.yaml` se commitea como
  template; el `viewers.yaml` real se agrega a `.gitignore`.

### Alternatives consideradas (al modelo de login-as-person)

- **Sólo YAML (el original)**: rompe el source-of-truth principle (People mapea
  las regiones, se duplica en YAML). Rechazado.
- **Sólo People, sin YAML fallback**: rompe los escape hatches (admin_dev,
  CI accounts, roles custom). Inflexible. Rechazado.
- **OIDC / JWT real**: fuera de scope (v2.0 local-only). Deferred a v3.0+.
- **`.env` con `VIEWER_MARILENE_REGION=Caribbean`**: no escala a listas de
  regiones y por cada persona. Rechazado.

### Alternatives consideradas (al formato YAML del archivo de fallback)

- **`.env` con `VIEWER_ADMIN_DEV_REGIONS=...`**: no escala a listas. Rechazado.
- **JSON**: igual expresividad pero sin comentarios. YAML preferido.
- **TOML**: posible pero menos familiar en data engineering. No seleccionado.

---

## Part D — Determinismo de `semantic_layer.json`

### Decision: Canonical JSON sin `generated_at` (timestamp solo en `.md`)

**Decision**: El `SemanticLayerDocument` se serializa en dos artefactos:

1. **`semantic_layer.json`** — canonical, **sin** `generated_at`, **sin** `source_file` (que es path-dependent), serializado con:
   ```python
   json_str = json.dumps(
       document.model_dump(exclude_none=True, exclude={"generated_at", "source_file"}),
       indent=2,
       sort_keys=True,
       ensure_ascii=False,
   )
   ```
   - `sort_keys=True` → claves en orden alfabético determinista.
   - `exclude_none=True` → campos opcionales vacíos no se incluyen.
   - `ensure_ascii=False` → UTF-8 native (las descriptions tienen acentos).
   - Sin `generated_at` → dos runs en el mismo estado de inputs producen el mismo sha256.
2. **`semantic_layer.md`** — human-readable, **sí** incluye `generated_at` y `source_file` (incluido un footer con el sha256 del `semantic_source` y del `load_manifest` para trazabilidad). No determinista en tiempo, pero su contenido semántico sí lo es.

### Rationale

- **Sin `generated_at` en el JSON**: el JSON se vuelve diffeable en git y verificable en tests. SC-005 (determinismo) pasa.
- **Trazabilidad**: el `.md` carga con el `source_sha256` del `load_manifest.json` (link al warehouse state) y un hash del `semantic_source.py` (link al código semántico). Esto da auditabilidad sin sacrificar determinismo.
- **`source_file` excluido**: es un path local (absoluto) que rompería el determinismo across machines. Se mantiene solo en el `.md`.

### Alternatives consideradas

- **Timestamps en ambos**: rompe SC-005. Rechazado.
- **Separar en dos modelos Pydantic (`SemanticLayerDocumentCanonical` vs `SemanticLayerDocument`)**: over-eng. Un solo modelo con `exclude` en serialización es suficiente.

---

## Part E — Boundary Enforcement: Cómo garantizar que ningún path bypassa RLS

### Decision: Composition en el `QueryProvider` (defensive design, no monkey-patching)

**Decision**: La garantía constitucional NON-NEGOTIABLE ("ningún SQL bypassa RLS") se enforce de varias formas complementarias:

#### 1. Composición en el `cli/main.py` (composition root)

El `TextToSqlPipeline` se construye con un `QueryProvider` ya envuelto en un `GovernedQueryProvider`:

```python
# cli/main.py (esqueleto)
def build_pipeline(viewer: SemanticViewer | None) -> TextToSqlPipeline:
    pg_repo = PostgresRepository(config=PostgresConfig.from_env())
    resolver = SemanticQueryResolver()  # pure
    query_provider: QueryProvider = (
        GovernedQueryProvider(delegate=pg_repo, resolver=resolver, viewer=viewer)
        if viewer is not None
        else pg_repo  # sin viewer → fail-fast on first execute_readonly_query call
                       # (no bypass silencioso; la clara invocación sin viewer lanza)
    )
    return TextToSqlPipeline(..., query_provider=query_provider)
```

`GovernedQueryProvider` es un wrapper (Decorator) que:
- Implementa el `QueryProvider` Protocol (no hay cambio de tipo para el pipeline).
- En `execute_readonly_query(sql, table_def)`:
  1. Si `viewer is None` → `raise ValueError("Governance is non-negotiable. Provide --viewer or --allow-full-access.")`. **Ninguna call silenciosa sin governance.**
  2. Si `viewer.allows_full_access` y `is_local_dev()` → loguea `gov.bypass` y delega el SQL original.
  3. Si no → aplica `apply_rls(sql, viewer, table_def)` y delega el SQL gobernado.
- `GovernedQueryProvider` vive en `src/data_engineering/semantic_layer/governed_provider.py`.

#### 2. Boundary test que fuerza el contrato

`tests/contract/test_boundaries.py` se extiende con:

```python
def test_no_path_bypasses_rls():
    """Constitution Principle IV: no LLM-generated SQL may bypass Semantic Layer RLS."""
    # All callers of execute_readonly_query must route through GovernedQueryProvider.
    # (Static / AST-based check via grep in tests/contract/test_boundaries.py)
    # 1. `execute_readonly_query` callers in src/ai_engineering/ must go through
    #    the QueryProvider Protocol (not call pg_repo directly).
    # 2. `GovernedQueryProvider` MUST wrap any PG/BQ repository used in CLI.
    # 3. No raw `cur.execute(sql)` in ai_engineering (already enforced in 002).
```

Esto es un test estático (AST/grep) — implementa la constitución como check automatizado.

#### 3. Integration test end-to-end

`tests/integration/test_semantic_rls.py` corre dos viewers con regions distintas, hace `ask "total sales"` con cada uno, y verifica que los totales de cada uno son los esperados de `SELECT SUM(Sales) WHERE Region IN (regions)` directo. Cualquier bypass se traduce en números idénticos entre viewers → el test falla.

### Rationale

- **Decorator pattern** (`GovernedQueryProvider`) sobre `PostgresRepository`: no modifica el adapter existente (que solo ejecuta SQL). El adapter sigue siendo agnóstico al engine y a governance; la capa de governance se compone encima. Compatible con un futuro `BigQueryRepository` sin cambios.
- **`viewer is None` en CLI lanza** (no auto-asume `allows_full_access`): la constitución es NON-NEGOTIABLE; el default es fail, no bypass.
- **AST/grep check en boundary test**: la constitución se "codifica" — cualquier contribucción futura que añada un caller nuevo de `execute_readonly_query` fuera del wrapper es atrapado automaticamente.
- **`GovernedQueryProvider` en data_engineering.semantic_layer**: es semántica/governance — Data Engineering owns la Semantic Layer. Compatible con la constitución.

### Alternatives consideradas

- **Modificar `QueryProvider` Protocol** para recibir `viewer`: rompería la firma existente (`execute_readonly_query(sql, table_def)` de 002) y cou plificaría gobernanza con data-access. Rejectado — el Decorator es más limpio.
- **PostgreSQL RLS policies a nivel DB** (`CREATE POLICY`): acopla governance a DDL, rompe idempotencia del bootstrap. Rejectado. (Descrito ya en Part A.)
- **Monkey-patch en runtime**: frágil y no tipable. Rejectado.

---

## Part F — Data-grounded facts que la implementación debe respetar

### Hechos del dataset (de `data_dictionary.md` y `data-model.md`)

| Hecho | Fuente | Implicación para el Semantic Layer |
|---|---|---|
| `Returns.Order ID` tiene 63 duplicados sobre 2,033 filas | `data_dictionary.md` Returns row | El LEFT JOIN para `net_sales` debe usar `EXISTS (SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID")` (no un JOIN directo que duplicaría filas). La fórmula de `returned_amount` debe diskontar `SUM(Sales)` deletando las líneas retornadas una sola vez por `Row ID`. |
| `Returns.Returned` es degenerate (siempre 'Yes') | `data_dictionary.md` | No se filtra por esa columna — la presence de la fila ya indica "retornado". |
| `Orders.Region` tiene 23 valores únicos | `data_dictionary.md` | El IN (...) puede tener hasta 23 valores; está bien (perf no es issue). |
| `People.Region` tiene 24 valores, divide `Canada` en Eastern/Western | `data_dictionary.md` People | El mapping `viewer → regions` puede usar regiones que no existen en `Orders` → el resolver las incluye en el IN (`Region IN ('Eastern Canada')`) y PostgreSQL devuelve 0 filas. Conservador; v3.0+ resuelve el mismatch. Se documenta. |
| `Orders.Sales` es `NUMERIC(12,4)` | `data-model.md` § 1 | Las métricas que usan `SUM(Sales)` devuelven `Numeric` → `QueryRow.data` lo mapea como `Decimal` o `float`. |
| `Orders.Profit` es signed (admite negativos) | `data-model.md` § 1 | `total_profit` puede ser negativo para algunas dimensiones — documentar. |
| `Orders."Order ID"` tiene 25,728 valores únicos sobre 51,290 filas | `data-model.md` § 1 | `COUNT(DISTINCT "Order ID")` para `order_count` y `avg_order_value` da 25,728 (distinto del row count). |

### Fórmulas SQL finales (canonizadas aquí, replicadas en `metrics.py` y `data-model.md`)

```sql
-- gross_sales: total de ventas brutas (suma de Sales sobre Orders)
gross_sales = SUM("Sales")

-- returned_amount: total de Sales de las líneas de Orders que tienen un Returns
returned_amount = SUM(CASE WHEN EXISTS (
    SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID"
) THEN "Sales" ELSE 0 END)

-- net_sales: gross minus returned
net_sales = SUM(CASE WHEN NOT EXISTS (
    SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID"
) THEN "Sales" ELSE 0 END)
-- Equivalente: gross_sales - returned_amount (definido asi en el SemanticLayerDocument.derives_from)

-- return_rate
return_rate = returned_amount / NULLIF(gross_sales, 0)

-- total_profit: SUM(Profit) signed
total_profit = SUM("Profit")

-- net_profit: total_profit proportionally minus returned_amount
-- (simplificación: descontamos el returned_amount del profit total; supone
-- que los returns no tienen profit propio asociado — documentado como asunción)
net_profit = SUM("Profit") - (
    SELECT COALESCE(SUM(o."Profit"), 0) FROM Orders o
    WHERE EXISTS (SELECT 1 FROM Returns r WHERE r."Order ID" = o."Order ID")
)
-- O si se quiere propagar el `derives_from` sin subquery:
-- net_profit = total_profit - (returned_amount / gross_sales * total_profit)
-- (proporcional; explicación completa en research.md)

-- avg_order_value (AOV)
avg_order_value = SUM("Sales") / NULLIF(COUNT(DISTINCT "Order ID"), 0)

-- order_count
order_count = COUNT(DISTINCT "Order ID")
```

**Nota sobre `net_profit`**: hay dos definiciones válidas. La canonizada aquí es la **proporcional** (`net_profit = total_profit - (returned_amount / gross_sales) * total_profit`), porque asume que los returns no tienen profit propio y descuenta el profit proporcionalmente a las ventas retornadas. Es una asunción razonable para el dataset synthetic. Documentada como `assumption` en el SemanticLayerDocument. Si la implementación necesita mayor precisión, la subquery alternativa está documentada en research.md y se puede activar via flag en v3.0+.

### Implicaciones para tests

- `test_semantic_resolver.py` (unit, no DB): cubre los casos de la Part A table.
- `test_semantic_rls.py` (integration, DB): crea dos viewers con regiones reales del dataset (e.g., `Caribbean` vs `Central US`), corre `ask` con cada uno, y compara `SUM(Sales)` devuelto por cada viewer con `SELECT SUM(Sales) FROM Orders WHERE "Region" = '<region>'` ejecutado directo. Cualquier bypass se traduce en números idénticos → test falla.

### Implicaciones para el `.md` artifact

El `semantic_layer.md` tendrá una sección "Dataset-Specific Notes" con:
- La nota sobre `Returns.Order ID` duplicates y cómo se maneja.
- El mismatch `People.Region` vs `Orders.Region` y que v3.0+ lo resuelve.
- La asunción de `net_profit`.

---

## Resumen de decisiones

| # | Tema | Decisión | Alternativas rechazadas |
|---|---|---|---|
| A | RLS strategy | Subquery wrapping con `WHERE "Region" IN (...)` externo | Regex WHERE rewrite; `sqlglot`; PostgreSQL RLS DDL |
| B | Prompt integration | Condensación selectiva (~+400 tokens): métricas + dimensiones + joins | Full JSON dump; system message separado; few-shot |
| C | Viewer config | `viewers.yaml` + `pyyaml` | `.env`; JSON; TOML; hardcoded |
| D | Determinism | JSON canonical sin `generated_at`; timestamp solo en `.md` | Timestamps en ambos; dos modelos separados |
| E | Boundary enforcement | `GovernedQueryProvider` decorator + AST/grep boundary test + integration test | Modificar Protocol; PostgreSQL RLS DDL; monkey-patch |
| F | Data-grounded formulas | Canonizadas arriba (incluye asunción de `net_profit` proporcional) | — |
