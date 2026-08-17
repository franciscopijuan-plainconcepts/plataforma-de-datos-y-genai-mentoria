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

## Part A — RLS Strategy: Subquery Wrapping (NO regex WHERE rewrite)

### Decision: Envolver el SQL en una subquery con `WHERE Region IN (...)` externo

**Decision**: El `SemanticQueryResolver.apply_rls(sql, viewer, table_def)` devuelve:

```sql
SELECT * FROM (
    <original_sql>
) AS _gov
WHERE "Region" IN ('R1', 'R2', ...)
```

Donde `<original_sql>` es el SQL generado por el LLM y ya validado por el `SqlValidator` (que garantiza: SELECT-only, single-statement, no comments, no forbidden keywords, Orders-only-table-naming, existing-columns-only).

**Rationale**:

- **No dependency on SQL parsing** — el `SqlValidator` ya garantiza que el SQL es un SELECT sobre `Orders` (con posibilidad de JOIN a `Returns` para métricas derivadas en esta feature). Envolverlo en una subquery con un WHERE externo es **composicionalmente seguro**: PostgreSQL lo ejecuta sin ambigüedad.
- **Robusto frente a WHERE existente** — si el LLM genera `SELECT ... FROM Orders WHERE Region = 'X'`, el wrapping resulta en `SELECT * FROM (SELECT ... WHERE Region = 'X') AS _gov WHERE "Region" IN ('R1', 'R2')` — la intersección natural (el viewer no ve regiones fuera de scope aunque el SQL las pida). Está alineado con la acceptance scenario 2 del US2.
- **Robusto frente a GROUP BY / ORDER BY / LIMIT** — el wrapper externo solo filtra filas; los `GROUP BY`, `ORDER BY`, `LIMIT`, aggregaciones del inner SQL quedan intactos. El wrapper no rompe la semántica de la consulta original.
- **Robusto frente a aggregaciones sin `Region` en SELECT** — si la query es `SELECT SUM(Sales) FROM Orders` (sin `Region` en SELECT ni GROUP BY), el wrapper añade `WHERE "Region" IN (...)` al outer y PostgreSQL lo propaga al inner via predicate pushdown (la columna `Region` existe en `Orders` y es accesible desde la subquery). Validado en tests de integración.
- **El alias `_gov` es estable** y nunca chocará con un alias del usuario (que tendría que ser `_gov` lowercase literal, extremadamente improbable y de todos modos el `SqlValidator` puede bloquearlo).
- **Case-sensitivity**: PostgreSQL dobla los identifiers no-comillados a lowercase; los identifiers del dataset son title-case (`"Region"`). El resolver SIEMPRE usa `Region` quoted con double-quotes para matchear la convención del dataset y del `SqlValidator` existente (que ya fuerza quoting en el prompt de 002).
- **Consistencia con el `SqlValidator`**: la feature 002 ya valida single-statement + SELECT-only + Orders-only. El resolver confía en esa garantía previa. Si un día se amplía la validación, el resolver se mantiene — el wrapper sigue siendo composicionalmente válido para cualquier SELECT.

### Alternatives consideradas

- **Regex para insertar `Region IN (...)` en el `WHERE` existente**: muy frágil (¿dónde está el `WHERE`? ¿Y si hay `HAVING`? ¿Y si el `WHERE` está dentro de un subquery?). Rejectado.
- **`sqlglot` (parser SQL)** — añade una dependencia nueva y complejidad de parsing para un problema que el wrapping resuelve elegantly. Rejectado por YAGNI (mismo argumento que en 002 para el `SqlValidator`).
- **Crear vistas SQL materializadas con RLS_POLICY** — rompería la idempotencia del `bootstrap` de la baseline (tendría que haber migraciones), acoplaría governance a DDL y complicaría la migración a BigQuery. Rejectado — governance en runtime layer, no en DDL.

### Casos edge

| Input | Output esperado |
|---|---|
| `SELECT * FROM Orders` (viewer regions: `[R1]`) | `SELECT * FROM (SELECT * FROM Orders) AS _gov WHERE "Region" IN ('R1')` |
| `SELECT SUM(Sales) FROM Orders` (viewer: `[R1, R2]`) | `SELECT * FROM (SELECT SUM(Sales) FROM Orders) AS _gov WHERE "Region" IN ('R1', 'R2')` ← PostgreSQL propaga el filtro a la subquery via pushdown |
| `SELECT Region, SUM(Sales) FROM Orders GROUP BY Region` (viewer: `[R1]`) | `SELECT * FROM (...) AS _gov WHERE "Region" IN ('R1')` ← ok |
| `SELECT * FROM Orders WHERE Region = 'R3'` (viewer: `[R1, R2]`) | wrapper → 0 filas (intersección vacía) — correcto, el viewer no ve R3 |
| Viewer con `regions: []` | `SELECT * FROM (...) AS _gov WHERE "Region" IN ()` es inválido; el resolver devuelve `SELECT * FROM (original) AS _gov WHERE FALSE` (devuelve 0 filas, PostgreSQL válido) |
| Viewer con `allows_full_access: True` (solo en ENV local/dev) | Devuelve el SQL original sin wrapper + loguea `gov.bypass` |
| SQL con `;` final | El `SqlValidator` ya lo habría rechazado (002); el resolver asume SQL limpio |

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

## Part C — Viewer Configuration Format: YAML

### Decision: `viewers.yaml` con `pyyaml` (nueva dependencia liviana)

**Decision**: Los viewers se configuran en un archivo `viewers.yaml`:

```yaml
# viewers.yaml — local, gitignored. Copy viewers.example.yaml to viewers.yaml and edit.
viewers:
  - id: alice
    regions:
      - Caribbean
      - Central America
    allows_full_access: false
  - id: bob
    regions:
      - Central US
      - Western US
    allows_full_access: false
  - id: admin_dev
    regions: []
    allows_full_access: true     # Solo efectivo cuando ENV in {local, dev, test}
  - id: caribbean_only
    regions:
      - Caribbean
    allows_full_access: false
```

Cargado por `src/data_engineering/semantic_layer/registry.py` con `pyyaml`. El path se resuelve:

1. `SEMANTIC_VIEWERS_FILE` env var (si está seteada → usar ese path).
2. Default: `viewers.yaml` en el cwd (raíz del proyecto).
3. Si no existe → el CLI falla rápido con un error claro listando cómo crearlo (apunta al `viewers.example.yaml`).

### Rationale

- **YAML sobre JSON**: YAML permite comentarios (`#`), mejor lectura para config humana, y multiline strings limpios. JSON no tiene comentarios — para un archivo que la persona va a editar frecuentemente, YAML gana.
- **YAML sobre `.env`**: `.env` es ok para pares k=v pero no escala a listas de regions por viewer. YAML estructura nativamente.
- **`pyyaml` es estándar**: ampliamente usado, mantenida, zero-surprise. Mismo coste que cualquier otra librería de parsing.
- **`.example.yaml` committed**: el `viewers.example.yaml` se commitea como template; el `viewers.yaml` real se agrega a `.gitignore` (contiene nombres de personas del negocio, incluso si son sample synthesize).

### Alternatives consideradas

- **`.env` con `VIEWER_ALICE_REGIONS="Caribbean,Central America"`**: verbs y no escala. Rechazado.
- **JSON**: igual expresividad pero sin comentarios. YAML preferido.
- **TOML**: posible pero menos familiar en data engineering; `pyyaml` es más estándar para este caso.
- **Definir viewers en código (`registry.py`)**: rompe la reproducibilidad across runs y deployments. Rechazado.

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
