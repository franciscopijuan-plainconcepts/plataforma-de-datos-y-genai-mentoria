# Research: Sales Prediction Model (MLOps v3.0)

**Feature**: 004-sales-prediction-model
**Date**: 2026-08-25
**Related**: [spec.md](./spec.md) · [plan.md](./plan.md)

> Este documento resuelve las decisiones técnicas abiertas del `plan.md` Technical Context. Cada parte sigue el formato Decision / Rationale / Alternatives considered.

> **Amendment (2026-08-25)**: `Product Name`/`City`/`State` were dropped from
> the feature set post-review (see `spec.md` § Amendment) — `Product ID` is
> now the only high-cardinality field handled by Part A's encoder below.

## Part A — Encoding de categóricas de alta cardinalidad para `LinearRegression`

**Decision**: Un `ColumnTransformer` con dos ramas:

1. **Baja/media cardinalidad** (`Ship Mode`, `Segment`, `Country`, `Region`, `Market`, `Sub-Category`, `Category`, y features temporales categóricas como día de la semana): `OneHotEncoder(handle_unknown="ignore", min_frequency=None)`. Estas columnas tienen entre 2 y ~20 valores únicos — one-hot no explota dimensionalidad.
2. **Alta cardinalidad** (`Product ID`, `Product Name`, `City`, `State`): un `FrequencyRareBucketEncoder` custom, sklearn-compatible (`fit`/`transform`, hereda de `BaseEstimator`/`TransformerMixin`), que:
   - En `fit`: calcula la frecuencia relativa de cada categoría vista en el training set y guarda el vocabulario (`category -> frequency`) más un umbral mínimo de frecuencia (p. ej. categorías que aparecen en <0.1% de las filas de train se consideran "raras").
   - En `transform`: reemplaza cada valor categórico por su frecuencia relativa (un `float` — no una columna binaria por categoría), y cualquier categoría no vista en `fit` (o por debajo del umbral) recibe el valor de frecuencia del bucket `"other"` (la frecuencia agregada de todas las categorías raras). Esto da una sola columna numérica por feature de alta cardinalidad, sin importar cuántas categorías únicas existan.
   - Determinista: mismo training set ⇒ mismo vocabulario de frecuencias ⇒ mismo output.

**Rationale**:
- **Evita explosión dimensional**: `Product ID`/`Product Name` tienen miles de valores únicos en Global Superstore (~1800+ productos); un one-hot directo generaría miles de columnas dispersas, degradando el fit de `LinearRegression` (colinealidad, overfitting, tiempos de entrenamiento) y contradiciendo FR-007 explícitamente.
- **Frequency encoding vs. target encoding**: se prefiere frequency encoding (frecuencia de aparición) sobre un target encoding clásico (media del target por categoría) porque el target encoding introduce fuga de información del target hacia el feature si no se aplica con validación cruzada out-of-fold cuidadosa — una complejidad adicional no justificada para un baseline interpretable cuyo propósito es servir de punto de comparación simple contra CatBoost (que ya maneja la señal categórica nativamente y con más sofisticación). Frequency encoding es determinista, no requiere CV interno, y sigue siendo una señal razonable (productos/ciudades más frecuentes tienden a tener patrones de venta distintos a los raros).
- **Bucket "other"** resuelve simultáneamente dos requisitos: (a) evita que categorías con 1-2 apariciones introduzcan ruido de alta varianza: se agrupan en una sola frecuencia agregada; (b) da una estrategia clara y determinista para categorías nunca vistas en inferencia (FR-024/AS3 de US4) — el valor "other" ya existe en el vocabulario aprendido, no hace falta un caso especial en `transform`.
- **CatBoost no necesita esto**: recibe las mismas columnas de alta cardinalidad vía `cat_features` sin ningún preprocesamiento manual (FR-008) — su algoritmo de ordered target statistics maneja alta cardinalidad internamente y de forma más sofisticada; comparar "CatBoost nativo" vs. "LinearRegression + frequency encoding simple" es precisamente la comparación que pide la spec (baseline interpretable vs. modelo de mayor capacidad).

**Alternatives considered**:
- **One-hot directo con `max_categories`** (sklearn `OneHotEncoder(max_categories=N)`): más simple de implementar (built-in), pero pierde granularidad de forma menos controlada (colapsa por orden de frecuencia interno de sklearn, no expone el vocabulario aprendido explícitamente para el reporte de `used_fallback_encoding` en inferencia). Rechazado por menor observabilidad, aunque se documenta como alternativa válida y más simple si se prefiriera menos código custom.
- **Target encoding con out-of-fold CV** (p. ej. `category_encoders.TargetEncoder` con validación cruzada interna): mejor señal predictiva potencial, pero agrega una dependencia nueva (`category_encoders`) y complejidad (K-fold interno dentro del `Pipeline`) para un baseline cuyo rol es ser simple e interpretable. Rechazado por YAGNI — el requisito (FR-007) pide "evitar explosión dimensional", no maximizar performance del baseline.
- **Hashing trick (`FeatureHasher`)**: determinista y sin vocabulario que mantener, pero introduce colisiones no interpretables (dos productos distintos podrían mapear al mismo hash bucket) y complica el reporte de `used_fallback_encoding`. Rechazado por simplicidad/interpretabilidad del baseline.

## Part B — Estrategia de split cronológico train/test

**Decision**: Ordenar todas las filas de `Orders` extraídas por `Order Date` ascendente y cortar por **proporción de filas** (no por fecha calendario fija): las primeras `(1 - test_fraction)` filas son train, las últimas `test_fraction` filas son test. `test_fraction` es un hiperparámetro versionado por run (default documentado: `0.2`, es decir ~20% más reciente como test). Se aplica un mínimo absoluto de filas de test (p. ej. `min_test_rows = 500`); si el dataset no alcanza para ese mínimo dado el `test_fraction` configurado, `train-sales-model` falla rápido con un mensaje explicando el requisito (edge case de la spec: "test set cronológico demasiado pequeño").

**Rationale**:
- **Por qué proporción y no fecha fija**: una fecha calendario fija (p. ej. "todo lo posterior al 2015-01-01 es test") puede producir splits degenerados si la distribución temporal de `Orders` es desigual (huecos, estacionalidad, o si el dataset se actualiza y la fecha fija deja de ser representativa). Cortar por proporción de filas ordenadas es robusto a estos huecos y siempre produce un split no-vacío mientras haya suficientes filas — resuelve directamente el edge case documentado en la spec ("¿Qué pasa si `Order Date` tiene huecos o el corte cronológico cae en un fin de semana/feriado?").
- **Por qué NO aleatorio**: `Order Date` es en sí una feature de entrada (día de semana, mes, día del mes, `is_weekend`); un split aleatorio permitiría que filas de fechas futuras aparezcan en train y fechas pasadas en test, lo cual es leakage temporal — el modelo podría aprender patrones que en producción real nunca tendría disponibles (no se puede entrenar con el futuro para predecir el pasado). Este es un requisito explícito y no-negociable de la spec (FR-006).
- **Función reutilizable**: la misma función (`src/mlops/split.py::chronological_split`) se llama para ambos modelos con el mismo `test_fraction`, garantizando FR-009 (comparación válida — mismas filas de train/test para ambos).

**Alternatives considered**:
- **Fecha de corte fija hardcodeada**: más simple, pero frágil ante cambios en el dataset fuente y no configurable/versionable por run. Rechazado.
- **Walk-forward / rolling-window cross-validation** (múltiples splits cronológicos): más robusto estadísticamente (media de métricas sobre varios cortes), pero agrega complejidad significativa (múltiples runs por modelo, agregación de métricas) no requerida por la spec, que pide explícitamente "un" split cronológico compartido para la comparación. Rechazado por YAGNI — puede evaluarse como mejora futura si el modelo se lleva a producción real.
- **Split por unidad de tiempo calendario (p. ej. "último mes completo = test")**: intuitivo para negocio, pero el tamaño del test set varía según la densidad de datos de ese período específico, reintroduciendo el riesgo de test sets no representativos. Rechazado en favor de proporción de filas.

## Part C — Formato del artifact registry / experiment tracking

**Decision**: Registry **basado en archivos**, sin servicio ni base de datos nueva:

- **Por-run (inmutable)**: `.artifacts/mlops/models/<model_name>/<run_id>/` con:
  - `params.json` — hiperparámetros del modelo + `test_fraction`/`min_test_rows` del split + versiones de librerías (`scikit-learn`/`catboost`) + timestamp de inicio.
  - `metrics.json` — RMSE, MAE, R², tamaño de train/test, fecha de corte cronológico (`split_cutoff_date`, la `Order Date` de la primera fila de test).
  - `data_hash.txt` — SHA-256 determinista del `FeatureSet` extraído (ver Part E).
  - `model.joblib` (LinearRegression) o `model.cbm` (CatBoost) — artifact serializado.
  - Este directorio se escribe con un patrón **todo-o-nada**: se construye primero en un directorio temporal (o se escriben todos los archivos y solo al final se hace un "commit" atómico, p. ej. escribir a `<run_id>.tmp/` y hacer `rename` al nombre final) — si cualquier paso falla, no queda un directorio parcial visible (FR-016).
- **Manifiesto global (mutable)**: `.artifacts/mlops/registry.json` — un documento único (`ArtifactRegistryDocument`) que lista, por `model_name`, todos los `run_id` conocidos con su timestamp y métricas resumidas (para listar sin deserializar el modelo, FR-015), y, por ambiente (`dev`/`staging`/`prod`), el `run_id` actualmente activo más el **historial completo** de promociones anteriores (timestamp, `run_id` promovido, actor — hardcodeado a un valor genérico dado que no hay auth real, per Assumptions de la spec) (FR-019).
- **Promoción**: `promote-sales-model` lee `registry.json`, valida que el `run_id` exista (si no, falla listando los disponibles — FR-020), aplica el gate `prod` requiere paso previo por `staging` (FR-018), y reescribe `registry.json` de forma atómica (mismo patrón write-temp-then-rename) agregando una entrada al historial en vez de sobrescribir la anterior.

**Rationale**:
- **Consistente con el mandato constitucional** de "no new cloud infra" para el entorno de desarrollo Docker/Postgres-only, y con el patrón de artifacts ya usado por `001` (`load_manifest.json`) y `003` (`semantic_layer.json`) — un documento JSON versionado, legible, sin dependencias de infraestructura adicionales.
- **`registry.json` inspeccionable sin deserializar el modelo** (FR-015): el manifiesto contiene toda la metadata resumida necesaria para listar runs; solo `predict-sales`/`promote-sales-model` necesitan resolver la ruta al artifact real, y solo la inferencia necesita cargarlo.
- **Historial en vez de solo estado vigente** (FR-019, US3 AC4): permite auditar qué modelo estuvo activo en cada ambiente en el pasado — un requisito explícito de gobernanza MLOps (Principle V: "traceable").
- **Escritura atómica (todo-o-nada)**: satisface FR-016 sin requerir una base de datos transaccional — un patrón estándar de sistemas de archivos (escribir a un path temporal + `os.rename`, que es atómico en la mayoría de sistemas de archivos POSIX para el mismo filesystem).

**Alternatives considered**:
- **SQLite como registry**: transaccional out-of-the-box, pero introduce un nuevo tipo de almacenamiento local que la constitución no exige (Principle III dice "PostgreSQL is the ONLY local data store" — un SQLite adicional para metadata de MLOps sería una excepción no justificada). Rechazado.
- **MLflow (local file-based tracking, `mlflow.set_tracking_uri("file:...")`)**: MLflow soporta un backend local basado en archivos sin servidor, lo cual técnicamente cumpliría "no new cloud infra". Sin embargo, agrega una dependencia pesada y su propio esquema de directorios/DB (`mlruns/`), acoplando el proyecto a la API y convenciones de MLflow para un caso de uso (2 modelos, 3 ambientes, un solo operador) que no lo necesita. Rechazado por YAGNI, explícitamente listado como "out of scope" en la spec ("Infraestructura cloud nueva... MLflow server").
- **Un archivo de registry por ambiente en vez de uno global**: más simple de escribir, pero dificulta un listado unificado de todos los runs conocidos (FR-015 pide poder listar independientemente del estado de promoción). Rechazado.

## Part D — Versiones de librerías nuevas

**Decision**: Se agregan a `pyproject.toml` `[project.dependencies]`:

```toml
"scikit-learn>=1.9",
"catboost>=1.2.10",
```

Verificado contra PyPI a la fecha de esta feature (2026-08-25): `scikit-learn` publica `1.9.0` como última versión estable; `catboost` publica `1.2.10`. Ambas son compatibles con Python 3.11+ (requisito de la constitución) y con el `python_version = "3.13"` configurado en `[tool.mypy]`.

**Rationale**:
- Pin con límite inferior (`>=`), consistente con el estilo ya usado en `pyproject.toml` para el resto de dependencias (`pydantic>=2.7`, `psycopg[binary]>=3.2`, etc.) — permite actualizaciones de patch/minor vía `uv.lock` sin re-editar el archivo, mientras el lockfile fija la versión exacta reproducible (constitution: "Dependency management: lockfile-based and reproducible").
- `numpy` (dependencia transitiva de ambas librerías) ya tiene un override en `[tool.mypy]` (`follow_imports = "skip"`, `ignore_errors = true`) porque el proyecto nunca la importa directamente (solo vía `pandas` hasta ahora). Este override sigue siendo válido: `src/mlops/` tampoco importa `numpy` directamente en su superficie pública tipada (los arrays de NumPy quedan confinados dentro de los métodos `fit`/`transform`/`predict` de scikit-learn/CatBoost, nunca cruzando el boundary de `src/contracts/mlops.py`).
- Se agregan overrides de mypy análogos para `sklearn.*` y `catboost.*` (`ignore_missing_imports = true`) porque ninguna de las dos librerías publica stubs completos compatibles con `mypy --strict` out-of-the-box; esto es consistente con el override ya existente para `psycopg.*`/`yaml.*`.

**Alternatives considered**:
- **Pin exacto (`==1.9.0`, `==1.2.10`)**: más determinista a primera vista, pero redundante dado que `uv.lock` ya fija la versión exacta reproducible; un pin exacto en `pyproject.toml` solo añadiría fricción para actualizaciones de seguridad/patch sin beneficio adicional. Rechazado, consistente con el estilo existente del archivo.
- **`xgboost` o `lightgbm` en vez de `catboost`**: ambos son alternativas de gradient boosting con manejo de categóricas (LightGBM nativamente, XGBoost con one-hot/encoding externo). Rechazado porque la spec pide explícitamente CatBoost por su manejo nativo de categóricas de alta cardinalidad sin encoding manual (`cat_features`), que es precisamente el contraste que se busca contra el baseline de `LinearRegression`.

## Part E — Estrategia de hash de datos para reproducibilidad

**Decision**: `data_hash = sha256(canonical_json(sorted(FeatureSet.rows, key=natural_row_order)))`, donde:
- Cada `SalesFeatureRow` se serializa a un `dict` canónico (mismo orden de claves, `sort_keys=True`, sin campos no determinísticos como timestamps de extracción).
- Las filas se ordenan de forma estable antes de hashear (por `order_date` y luego por un identificador estable derivado — nunca el orden de llegada desde la base de datos, que no está garantizado determinista entre corridas por PostgreSQL sin `ORDER BY` explícito).
- El hash resultante se persiste como `data_hash.txt` en cada run.

**Rationale**:
- **Reproducibilidad verificable (FR-014, SC-003)**: dos corridas de `train-sales-model` sobre el mismo estado de `Orders` y los mismos hiperparámetros deben producir el mismo `data_hash` y, por lo tanto, poder afirmarse formalmente que están comparando datos idénticos (no solo "asumiendo" que la tabla no cambió).
- **Ordenar antes de hashear** es crítico: sin un `ORDER BY` determinista, PostgreSQL puede devolver filas en distinto orden entre ejecuciones incluso si el contenido no cambió, lo que produciría hashes distintos para datos idénticos — un falso negativo de reproducibilidad. Se ordena explícitamente en `src/mlops/dataset.py` antes de calcular el hash (y antes del split cronológico, que de todas formas requiere orden por `Order Date`).
- **Consistente con el patrón ya usado en `001`** (`sha256_of_file` sobre el workbook fuente, en `src/data_engineering/ingestion/manifest.py`) — misma técnica (SHA-256 sobre una representación canónica), aplicada aquí a datos extraídos en vez de a un archivo.

**Alternatives considered**:
- **Hash del `load_manifest.json` completo (`source_sha256`) en vez de un hash propio del `FeatureSet`**: más simple (reutilizar el hash existente de `001`), pero menos preciso — dos runs podrían compartir el mismo `source_sha256` (mismo archivo fuente cargado) pero derivar `FeatureSet`s distintos si el código de feature engineering cambia entre versiones. Se decide computar un hash propio del `FeatureSet` *después* de la derivación de features, para que capture también cambios en la lógica de feature engineering, no solo en los datos fuente. El `source_sha256` del manifiesto SÍ se registra adicionalmente en `params.json` como metadata de provenance complementaria, no como el `data_hash` primario.
- **Hash por fila (Merkle-like) en vez de un solo hash sobre el conjunto**: permitiría detectar qué filas específicas cambiaron, pero es una complejidad no requerida por ningún FR/SC de la spec (que solo pide "mismos datos ⇒ mismas métricas", una comparación binaria). Rechazado por YAGNI.

## Part F — Manejo de categorías no vistas en inferencia (`used_fallback_encoding`)

**Decision**:
- **`LinearRegression`**: el `OneHotEncoder(handle_unknown="ignore")` ya absorbe categorías nuevas en las columnas de baja/media cardinalidad (las codifica como todo-ceros, sin error). El `FrequencyRareBucketEncoder` (Part A) mapea cualquier categoría no vista en su vocabulario aprendido al valor de frecuencia del bucket `"other"`, también sin error.
- **`CatBoostRegressor`**: maneja categorías nuevas en `predict` de forma nativa (las trata según su lógica interna de ordered statistics, sin lanzar excepción).
- **Señalización (`used_fallback_encoding: bool`)**: `src/mlops/inference.py` compara, para cada columna categórica del `PredictionInput`, el valor recibido contra el vocabulario de categorías vistas en entrenamiento (persistido junto al artifact — el `FrequencyRareBucketEncoder` y las categorías del `OneHotEncoder` exponen su vocabulario aprendido vía sklearn `categories_`/atributo custom). Si **cualquier** columna categórica del input no estaba en el vocabulario de entrenamiento del modelo activo, `PredictionResult.used_fallback_encoding = True`.

**Rationale**:
- Satisface FR-024 (nunca lanzar excepción no controlada ante categoría nueva) reutilizando comportamiento built-in de sklearn/CatBoost en vez de lógica custom de manejo de errores — más robusto y menos código propio.
- Satisface US4 AC3 (señalizar el fallback explícitamente en la respuesta) computando la comparación de vocabulario en la capa de inferencia, no dentro del modelo mismo — mantiene el modelo serializado como la única fuente de verdad del vocabulario aprendido (no hay estado duplicado que pueda desincronizarse).

**Alternatives considered**:
- **Try/except alrededor de `predict()` para capturar errores de categoría desconocida**: innecesario dado que ni sklearn (`handle_unknown="ignore"`) ni CatBoost lanzan excepción por esto de por sí; envolver en try/except sin necesidad ocultaría otros errores reales de forma menos clara. Rechazado.
- **Vocabulario de referencia separado (archivo aparte) en vez de leerlo del modelo serializado**: duplicaría el estado (el vocabulario ya vive dentro del `Pipeline`/encoder ajustado); mantenerlos sincronizados agregaría complejidad sin beneficio. Rechazado.

## Part G — Cómo `src/mlops/` lee `Orders` sin bypass del `QueryProvider`

**Decision**: `src/mlops/dataset.py` construye un SQL de solo-lectura **literal y confiable** (`SELECT * FROM "Orders"`, sin interpolación de input externo — no hay LLM ni usuario final involucrado en su construcción) y lo pasa por el `SqlValidator` existente (`src/ai_engineering/sql_validator.py::validate_sql`, reutilizado tal cual, sin modificar) antes de invocar `QueryProvider.execute_readonly_query(sql, table_def)`. El resultado (`list[QueryRow]`) se mapea a `SalesFeatureRow` en `src/mlops/features.py`.

**Rationale**:
- **FR-001 exige**: "el sistema MUST leer los datos de entrenamiento EXCLUSIVAMENTE a través del `QueryProvider`... ningún módulo bajo `src/mlops/` MUST importar `psycopg`/drivers de base de datos ni construir SQL crudo directamente". Reutilizar `execute_readonly_query` (ya parte del Protocol `QueryProvider` desde `001`, consumido hoy por `ai_engineering`) satisface esto sin requerir cambios al Protocol ni al adapter Postgres — cero acoplamiento nuevo a PostgreSQL específicamente, y la migración futura a BigQuery (Principle III) no requiere ningún cambio en `src/mlops/`.
- **Reutilizar `SqlValidator`** (en vez de omitir la validación porque "no hay LLM de por medio") añade una capa de defensa en profundidad consistente y barata: si en el futuro el SQL de extracción se volviera parametrizable (p. ej. un rango de fechas de entrenamiento configurable por CLI), ya está validado que solo se aceptan `SELECT` de una sola tabla, sin necesidad de re-auditar el código de `mlops` para inyección. También evita divergencia arquitectónica: todo el código del repo que llama `execute_readonly_query` pasa primero por `validate_sql`, sin excepciones caso-por-caso.
- **Sin `GovernedQueryProvider` (RLS) inyectado** en este path (ver Constitution Check Principle IV en `plan.md`): el training es un job de sistema sobre el dataset agregado completo, no una consulta por-viewer. `execute_readonly_query` se invoca directamente sobre el `QueryProvider` base (`PostgresRepository`), no sobre el wrapper de RLS de `003` (que requeriría un `SemanticViewer` con sentido para modelar "el training ve todo").

**Alternatives considered**:
- **Agregar un método nuevo al `QueryProvider` Protocol** (p. ej. `fetch_all(table_name) -> list[QueryRow]`): cambiaría un contrato compartido ya estable, con impacto en todos los consumers existentes (`ai_engineering`) y en los tests de conformidad del Protocol (`tests/contract/test_boundaries.py`); no aporta valor sobre reutilizar `execute_readonly_query`, que ya cubre el caso de "SELECT sin filtro" (es lo que hace `find_orders_by_region` para un caso filtrado). Rechazado por YAGNI y por evitar romper compatibilidad de un contrato compartido.
- **Reutilizar `DataProvider.find_orders_by_region`** iterando sobre todas las regiones conocidas y concatenando resultados: evitaría tocar `execute_readonly_query`, pero requiere conocer de antemano el conjunto de regiones (acoplamiento a datos), hace N round-trips en vez de uno, y semánticamente `find_orders_by_region` está documentado como el "future v2.0 RLS hook" (un método de negocio, no de extracción masiva para ML). Rechazado.
