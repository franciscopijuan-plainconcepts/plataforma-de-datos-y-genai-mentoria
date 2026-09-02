# Quickstart: Sales Prediction Model (MLOps v3.0)

**Feature**: 004-sales-prediction-model
**Date**: 2026-08-25
**Related**: [spec.md](./spec.md) · [plan.md](./plan.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/)

> Runnable validation guide del dominio MLOps v3.0. Cubre: entrenamiento comparado de ambos modelos, inspección del artifact registry, promoción por ambiente, y predicción sobre el modelo promovido. Es una guía de validación — los detalles de implementación viven en `tasks.md`.

## Prerequisites

- **Baseline (v0) corriendo**: el warehouse Postgres en Docker con `Orders` cargada.
  ```bash
  uv run python -m src.cli.main validate
  ```
  Debe imprimir `VALIDATION PASSED`. Si falla, correr `bootstrap` primero (ver [quickstart de 001](../001-data-genai-platform-baseline/quickstart.md)).

- **Dependencias nuevas `scikit-learn`/`catboost`**: se agregan a `pyproject.toml`. Tras actualizar:
  ```bash
  uv sync
  ```
  **Expected**: `scikit-learn>=1.9` y `catboost>=1.2.10` quedan en el lockfile; `uv sync` termina sin errores.

- No se requiere `FORGE_API_KEY` ni ninguna otra credencial de LLM para esta feature (v3.0 no usa el LLM — es puramente MLOps sobre datos ya cargados).

## Setup (one-time)

### 1. Instalar las nuevas dependencias

```bash
uv sync
```

### 2. Confirmar el estado del warehouse

```bash
uv run python -m src.cli.main validate
```

**Expected**: `VALIDATION PASSED` (container up, `Orders`/`Returns`/`People` cargadas, manifest presente).

## Scenario 1 — Entrenar y comparar ambos modelos (US1, MVP)

```bash
uv run python -m src.cli.main train-sales-model
```

**Expected output** (formato ilustrativo — el CLI real puede variar en presentación exacta, no en contenido):

```text
Extracting Orders via QueryProvider... 51290 rows.
data_hash=<sha256 hexdigest>
Chronological split: train=41032 rows, test=10258 rows (cutoff order_date=<fecha>)

Training linear_regression...
Training catboost...

Model comparison (same test set, same split):
┌──────────────────┬─────────┬────────┬───────┬──────────────────┐
│ model             │ rmse    │ mae    │ r2    │ training_time_ms │
├──────────────────┼─────────┼────────┼───────┼──────────────────┤
│ linear_regression │  <val>  │ <val>  │ <val> │ <val>            │
│ catboost          │  <val>  │ <val>  │ <val> │ <val>            │
└──────────────────┴─────────┴────────┴───────┴──────────────────┘
Better RMSE: catboost (run_id=<run_id_catboost>)

Persisted runs:
  linear_regression -> .artifacts/mlops/models/linear_regression/<run_id_linear>/
  catboost          -> .artifacts/mlops/models/catboost/<run_id_catboost>/
```

**Validation checks**:
- Exit code `0`.
- Ambos `run_id` son distintos entre sí y distintos de cualquier corrida previa.
- El CLI identifica explícitamente cuál modelo tuvo mejor RMSE (FR-012).
- Ninguna excepción no controlada, aún si Postgres estuviera caído (en ese caso: exit code no-cero con mensaje claro, sin runs parciales — probarlo apagando el container y re-corriendo el comando).

## Scenario 2 — Inspeccionar el artifact registry sin deserializar el modelo (US2)

```bash
cat .artifacts/mlops/registry.json | python -m json.tool
```

**Expected**: un documento JSON con:
- `runs`: al menos las dos entradas de Scenario 1 (`linear_regression`, `catboost`), cada una con `run_id`, `trained_at`, y `metrics` (`rmse`/`mae`/`r2`/`test_row_count`/`split_cutoff_date`) — visible **sin** necesidad de cargar `model.joblib`/`model.cbm` (FR-015).
- `promotion_history`: vacío en esta primera corrida (nada promovido todavía).

También inspeccionar un run individual:

```bash
ls .artifacts/mlops/models/catboost/<run_id_catboost>/
cat .artifacts/mlops/models/catboost/<run_id_catboost>/params.json | python -m json.tool
cat .artifacts/mlops/models/catboost/<run_id_catboost>/metrics.json | python -m json.tool
cat .artifacts/mlops/models/catboost/<run_id_catboost>/data_hash.txt
```

**Expected**: `params.json` incluye hiperparámetros + versiones de librería
(`{"catboost": "1.2.10", ...}`); `metrics.json` coincide con lo mostrado en el
CLI; `data_hash.txt` contiene un hash SHA-256.

**Reproducibility check** (FR-014/SC-003): correr `train-sales-model` de
nuevo sin cambiar datos ni código, y comparar:

```bash
uv run python -m src.cli.main train-sales-model
diff <(cat .artifacts/mlops/models/catboost/<run_id_1>/data_hash.txt) \
     <(cat .artifacts/mlops/models/catboost/<run_id_2>/data_hash.txt)
```

**Expected**: los `data_hash.txt` son idénticos (mismo dataset), y las
métricas en ambos `metrics.json` son idénticas byte a byte (mismos
hiperparámetros por defecto, mismo split determinista).

## Scenario 3 — Promover un modelo a un ambiente (US3)

```bash
uv run python -m src.cli.main promote-sales-model --run-id <run_id_catboost> --env staging
```

**Expected**: exit code `0`; `registry.json::promotion_history` gana una
entrada nueva (`environment=staging`, `run_id=<run_id_catboost>`, `promoted_at=<timestamp>`).

Intentar promover DIRECTAMENTE a `prod` sin haber pasado por `staging` con
un `run_id` distinto (p. ej. el de `linear_regression`, que en este escenario
nunca fue promovido a `staging`):

```bash
uv run python -m src.cli.main promote-sales-model --run-id <run_id_linear> --env prod
```

**Expected**: RECHAZADO — exit code no-cero, mensaje claro indicando que
`<run_id_linear>` debe pasar primero por `staging` (FR-018/US3 AC2).

Ahora promover el `run_id` de `catboost` (que SÍ pasó por `staging` en el
paso anterior) a `prod`:

```bash
uv run python -m src.cli.main promote-sales-model --run-id <run_id_catboost> --env prod
```

**Expected**: exit code `0` — permitido porque `<run_id_catboost>` ya está en
el historial de `staging`.

Intentar promover un `run_id` inexistente:

```bash
uv run python -m src.cli.main promote-sales-model --run-id does-not-exist --env dev
```

**Expected**: RECHAZADO — mensaje claro listando los `run_id` disponibles (FR-020).

## Scenario 4 — Predecir Sales sobre el modelo promovido (US4)

> **Amendment (2026-08-25)**: `--city`/`--state`/`--country`/`--product-name`
> fueron removidos de `predict-sales` (ver `spec.md` § Amendment). Los
> ejemplos abajo usan el conjunto de flags final.

```bash
uv run python -m src.cli.main predict-sales --env prod \
  --ship-mode "Second Class" \
  --segment "Consumer" \
  --region "West" \
  --market "US" \
  --product-id "TEC-AC-10003033" \
  --sub-category "Accessories" \
  --category "Technology" \
  --quantity 3 \
  --discount 0.0 \
  --order-date 2026-08-20
```

**Expected output** (ilustrativo):

```text
Predicted Sales: <numeric value>
Model: catboost (run_id=<run_id_catboost>, environment=prod)
used_fallback_encoding: false
latency_ms: <value < 2000>
```

**Validation checks**:
- `predicted_sales` es un número (no una excepción).
- `latency_ms < 2000` (SC-006).
- La invocación queda logueada en `.artifacts/mlops/predict_sales.log` (verificar con `tail -n 5 .artifacts/mlops/predict_sales.log`).
- **(Amendment 2026-08-26)** La invocación además queda persistida como una fila nueva en la tabla SQL `Predictions` (creada por `bootstrap`, idempotente si no existía). Verificar:
  ```bash
  docker exec plataforma_postgres psql -U postgres -d superstore \
    -c 'SELECT * FROM "Predictions" ORDER BY "Predicted At" DESC LIMIT 5;'
  ```
  Si Postgres no está disponible, `predict-sales` sigue funcionando (solo con el log JSONL) — la persistencia SQL es best-effort.

### Scenario 4b — Categoría no vista en entrenamiento (US4 AC3)

```bash
uv run python -m src.cli.main predict-sales --env prod \
  --ship-mode "Second Class" --segment "Consumer" \
  --region "West" --market "US" \
  --product-id "BRAND-NEW-PRODUCT-ID-NEVER-SEEN" \
  --sub-category "Accessories" --category "Technology" \
  --quantity 1 --discount 0.0 --order-date 2026-08-20
```

**Expected**: exit code `0` (NO lanza excepción); `used_fallback_encoding: true` en el output (FR-024).

### Scenario 4c — Ambiente sin modelo promovido

```bash
uv run python -m src.cli.main predict-sales --env staging \
  --ship-mode "Second Class" --segment "Consumer" \
  --region "West" --market "US" --product-id "TEC-AC-10003033" \
  --sub-category "Accessories" --category "Technology" \
  --quantity 3 --discount 0.0 --order-date 2026-08-20
```

(Nota: `staging` sí tiene un modelo promovido en este flujo de ejemplo desde
Scenario 3 — para reproducir este caso específico, usar un ambiente que
nunca haya recibido `promote-sales-model`, p. ej. `dev` si no se promovió
nada allí todavía.)

**Expected**: exit code no-cero, mensaje claro indicando que no hay modelo
activo en ese ambiente (FR-022).

## Cleanup

No hay recursos adicionales que limpiar más allá del baseline (`teardown` de
`001` sigue aplicando para el container Postgres). El artifact registry
(`.artifacts/mlops/`) puede borrarse manualmente para empezar de cero:

```bash
rm -rf .artifacts/mlops/
```

## Summary of CLI commands introduced by this feature

| Command | Purpose | Key flags |
|---|---|---|
| `train-sales-model` | Extrae Orders, split cronológico, entrena `LinearRegression` + `CatBoostRegressor`, evalúa, persiste ambos runs. | (ninguno obligatorio; hiperparámetros por defecto documentados) |
| `promote-sales-model` | Promueve un `run_id` existente a un ambiente. | `--run-id`, `--env {dev,staging,prod}`, `--force` (bypass del gate staging→prod, logueado) |
| `predict-sales` | Predice `Sales` usando el modelo activo de un ambiente. | `--env {dev,staging,prod}`, + los 10 campos de `PredictionInput` (ver `data-model.md` § 7; reducido de 14 tras el Amendment) |

## Amendment (2026-09-02): Batch forecast seeding (v3.1, no LLM)

> **Decisión**: poblar un dashboard con "Sales predicho para los próximos
> meses" es una tarea **batch**, no algo que el LLM deba disparar a petición
> del cliente — el LLM nunca invoca el modelo directamente (Principle I).
> Tanto `predict-sales-nl` (pregunta ad-hoc parseada por LLM) como el nuevo
> seeder batch pasan por la MISMA función tipada `predict_sales()` de este
> feature; para inputs de forecast deterministas no hay razón para pagar
> latencia/costo/riesgo de fallo de LLM en tiempo de siembra.

Añade `src/mlops/seed_predictions.py` (nuevo módulo, sin modificar ningún
contrato de esta spec) + el comando CLI `seed-sales-predictions`:

```bash
uv run python -m src.cli.main seed-sales-predictions --env prod --months-ahead 6 [--force]
```

**Comportamiento**:
1. Si no hay modelo promovido en `--env`, entrena + promueve uno
   automáticamente (mejor RMSE entre `linear_regression`/`catboost`,
   siguiendo el mismo gate `dev`→`staging`→`prod` de `promote-sales-model`).
2. Extrae las 10 combinaciones `(Region, Category)` más frecuentes de
   `Orders` (vía `extract_feature_set`, el mismo extractor de
   `train-sales-model`) y construye un perfil "orden típica" por
   combinación (moda de `ship_mode`/`segment`/`market`/`sub_category`/
   `product_id`, mediana de `quantity`).
3. Para cada perfil × cada uno de los próximos `--months-ahead` meses,
   construye un `PredictionInput` y llama a `predict_sales()` — la misma
   función usada por `predict-sales`/`predict-sales-nl` — persistiendo cada
   resultado en la tabla `Predictions` (data-model.md § 9), sin lógica de
   predicción paralela.
4. **Idempotente**: si el `run_id` activo ya tiene filas futuras en
   `Predictions`, la siembra se salta (`--force` para reseeded).

**Integración con `bootstrap`** (`001`/`quickstart.md`): al final de
`bootstrap`, tras crear la tabla `Predictions`, se invoca este flujo
automáticamente y en modo best-effort (nunca hace fallar `bootstrap`, ver
`SEED_SALES_PREDICTIONS=false` en `.env.example` para desactivarlo). Esto
significa que, tras un solo `uv run python -m src.cli.main bootstrap` en un
clon limpio, `Predictions` ya contiene forecasts listos para Metabase, sin
pasos manuales de `train-sales-model`/`promote-sales-model`/`predict-sales`.

**Validación** (verificado end-to-end el 2026-09-02):
```bash
rm -rf .artifacts/mlops/
uv run python -m src.cli.main bootstrap
# -> "Seeded 60 future predictions into 'Predictions' (env=prod)."
uv run python -m src.cli.main bootstrap
# -> "Future predictions already present for the active model — skipped."
uv run python -m src.cli.main seed-sales-predictions --env prod --force --months-ahead 3
# -> "Seeded 30 future predictions into 'Predictions' (env=prod, months_ahead=3)."
```

Esto NO cambia el alcance "Explicitly out of scope" original (§ arriba en
`spec.md`: sigue sin haber serving HTTP/API en tiempo real) — sigue siendo
100% batch/CLI, ahora simplemente invocado automáticamente por `bootstrap`
en lugar de requerir un paso manual.

## Amendment (2026-09-02): Text-to-SQL sobre `Predictions` (v3.2, sin nuevo LLM call)

> **Decisión**: el pipeline gobernado `ask`/`chart` (y por extensión
> `app_web.py`, que invoca `ask` vía subprocess) ahora puede generar SQL
> contra la tabla `Predictions` (forecasts ya sembrados por el amendment
> anterior), además de `Orders`. Esto es SOLO LECTURA sobre forecasts ya
> calculados — el LLM sigue sin invocar el modelo directamente (misma
> garantía de Principle I que `predict-sales-nl`, ahora extendida a
> analítica ad-hoc sobre el histórico de forecasts).

Cambios (todos en el pipeline core, `app_web.py`/`ask_metabase.py` NO se
tocan — son clientes finos que ya se benefician automáticamente):

1. `SqlValidator.validate_sql(sql, table_def, extra_tables=None)` — nuevo
   parámetro opcional `extra_tables: dict[str, TableDef]`. A diferencia del
   caso especial pre-existente de `"returns"` (que nunca necesitó columnas
   propias whitelisted, porque sus metric-patterns solo referencian `Order
   ID`, compartida con `Orders`), `Predictions` expone columnas que NO
   existen en `Orders` (`Predicted Sales`, `Predicted At`, `Run ID`, `Model
   Name`, `Environment`, `Used Fallback Encoding`, `Latency Ms`) — por eso
   `extra_tables` también fusiona esas columnas en `allowed_columns`, no
   solo el nombre de tabla en `allowed_tables`.
2. `build_prompt(..., extra_tables=None)` (`src/ai_engineering/prompt_builder.py`)
   — renderiza un bloque de schema independiente por cada tabla extra
   (columnas + tipos, tomados directo del `TableDef`, ya que `Predictions`
   es Postgres-only y no viene del diccionario generado desde el Excel) y
   añade una regla explícita: el LLM PUEDE consultar `Predictions` en su
   propio `FROM` (sin JOIN con `Orders`) para preguntas sobre forecast/sales
   futuro.
3. `TextToSqlPipeline(..., extra_tables=None)` (`src/ai_engineering/pipeline.py`)
   — hilvana `extra_tables` hacia `build_prompt` y `validate_sql`.
4. `_run_ask_pipeline()` (`src/cli/main.py`, usado por `ask`/`chart`) —
   construye `predictions_table_def()` (`src/mlops/predictions_store.py`) e
   inyecta `extra_tables={"predictions": predictions_table_def()}` al
   construir el pipeline.
5. RLS sigue aplicando sin cambios en `SemanticQueryResolver`: `Predictions`
   tiene su propia columna `Region`, y el resolver inyecta el predicado
   `WHERE "Region" IN (...)` directamente en el texto SQL (regex, agnóstico
   de tabla) — un viewer sigue viendo solo los forecasts de su(s) región(es).

**Bugs pre-existentes corregidos** (descubiertos al validar esta feature,
bloqueaban probar preguntas reales sobre `Predictions` con filtros de
texto):
- `SqlValidator._extract_identifiers` no despojaba literales de string entre
  comillas simples (p. ej. `WHERE "Region" = 'Caribbean'`) antes de extraer
  identificadores, por lo que `caribbean` se rechazaba como columna
  inexistente. Ahora también se despojan literales `'...'` (con `''`
  escapado) antes de tokenizar.
- `LlmClient.generate_sql` no despojaba un posible fence ```` ```sql ... ``` ````
  si el LLM ignoraba la regla "no markdown" (más probable en preguntas de
  agregación/agrupación más largas). Ahora se limpia un fence que envuelva
  la respuesta completa antes de devolver `GeneratedSql`.

**Validación** (verificado end-to-end el 2026-09-02, con `Predictions` ya
sembrada por el amendment anterior):
```bash
uv run python -m src.cli.main ask \
  "What is the average predicted sales for the South America region?" \
  --viewer <persona_de_esa_region>
# -> Generated SQL: SELECT AVG("Predicted Sales") FROM "Predictions" WHERE "Region" = 'South America';
# -> Validation: ACCEPTED

uv run python -m src.cli.main ask \
  "Show predicted sales by region for the next few months, ordered from highest to lowest"
# -> Generated SQL: SELECT "Region", SUM("Predicted Sales") AS predicted_sales
#    FROM "Predictions" WHERE "Order Date" >= CURRENT_DATE
#    AND "Order Date" < CURRENT_DATE + INTERVAL '3 months'
#    GROUP BY "Region" ORDER BY predicted_sales DESC;
# -> Validation: ACCEPTED, 10 rows returned

# Orders sigue funcionando sin cambios (regresión verificada):
uv run python -m src.cli.main ask "What is the total sales amount?"
# -> Generated SQL: SELECT SUM("Sales") FROM "Orders"; -> ACCEPTED
```

Suite de tests (`tests/unit/test_sql_validator.py`,
`tests/contract/test_text_to_sql.py`, `tests/contract/test_semantic_layer.py`,
`tests/contract/test_boundaries.py`, y el resto de `tests/unit`+`tests/contract`)
pasa sin regresiones tras este amendment.

## Amendment (2026-09-02): Comparar Orders vs Predictions (CTE + subqueries) en el validador

> Descubierto probando el amendment anterior a través de `app_web.py`: la
> pregunta más natural para este feature — "compara ventas reales vs
> predichas por región" — fallaba intermitentemente. El LLM resuelve esta
> comparación de dos formas equivalentes según el prompt/temperatura: (a) un
> `WITH actual AS (...), predicted AS (...) SELECT ...` (CTE), o (b)
> `FROM (SELECT ...) o JOIN (SELECT ...) p ON ...` (subqueries derivadas con
> alias). Ninguna de las dos pasaba el validador:

1. **CTEs rechazadas de raíz**: `"with"` estaba en `_FORBIDDEN_KEYWORDS`
   (bloqueo total, sin distinguir un `WITH` de solo lectura de uno
   malicioso) y `validate_sql` exigía que el SQL empezara literalmente por
   `SELECT`. Ahora: se permite empezar por `WITH` (se sigue rechazando
   `WITH RECURSIVE` explícitamente — riesgo de agotamiento de recursos sin
   caso de uso legítimo aquí), y los nombres de CTE (`WITH <nombre> AS (`)
   se extraen y se tratan como tablas virtuales permitidas SOLO dentro de
   esa sentencia (no se añaden a `allowed_tables` globalmente). Cualquier
   keyword de escritura (`INSERT`/`UPDATE`/`DELETE`/...) sigue bloqueado
   sin importar si está envuelto en un CTE.
2. **Alias de subquery derivada no reconocidos**: `FROM (SELECT ...) o` —
   el extractor de alias de tabla solo reconocía `FROM <tabla> <alias>`,
   no `FROM (<subquery>) <alias>`, así que `o`/`p` se marcaban como
   "columna inexistente" al usarse como `o."Region"`. Ahora se extrae
   también el identificador inmediatamente después de un `)` de cierre
   (con o sin `AS`) como alias de tabla válido.
3. El prompt (`prompt_builder.py`) ya NO le dice al LLM "no unas Predictions
   con Orders" a secas — ahora le indica explícitamente el patrón correcto:
   agregar cada tabla por separado (su propio `GROUP BY`) en su propio CTE
   o subquery, y unir los agregados por la dimensión compartida (p. ej.
   Region), nunca un JOIN fila-a-fila (`Predictions` no tiene una fila por
   pedido histórico, son perfiles representativos).

**Validación** (verificado con 3 intentos consecutivos, ambos estilos de
SQL generados por el LLM, el 2026-09-02):
```bash
uv run python -m src.cli.main ask \
  "Compara las ventas reales con las predichas por región" \
  --allow-full-access
# -> Validation: ACCEPTED (CTE-style o subquery-style, ambos aceptados)
# -> Rows: una fila por Region con actual_sales y/o predicted_sales
```

Suite de tests completa (`tests/unit` + `tests/contract`) sigue en verde
(210 passed) tras este amendment; también se corrió la suite `tests/`
completa incluyendo integración (220 passed, 2 skipped) sin regresiones.


