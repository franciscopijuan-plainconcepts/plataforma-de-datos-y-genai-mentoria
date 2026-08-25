# Data Model: Sales Prediction Model (MLOps v3.0)

**Feature**: 004-sales-prediction-model
**Date**: 2026-08-25
**Source**: Derived from `plan.md` Technical Context + `research.md` Parts A–G

> Este data model define los **contract models del dominio MLOps** (Pydantic v2, `frozen`) que viven en `src/contracts/mlops.py`. NO modifica ningún contrato existente (`data_access.py`, `text_to_sql.py`, `dictionary.py`, `semantic_layer.py`) — esos siguen sin cambios. `src/mlops/` consume `OrderRow`/`QueryRow`/`TableDef` (existentes) como input y produce/consume únicamente los modelos definidos aquí a través de sus límites de módulo (constitution Principle I).

> **Amendment (2026-08-25)**: `city`, `state`, `country`, `product_name` fueron
> removidos de `SalesFeatureRow`/`PredictionInput` tras revisar cardinalidades
> en `data_dictionary.md` — ver `spec.md` § Amendment para el razonamiento
> completo (redundancia geográfica con `region`/`market`; `product_name` es
> un duplicado bijectivo de `product_id`).

## Entities

### 1. `SalesFeatureRow`

Representación tipada de una fila lista para entrenar o predecir. Deriva de `OrderRow` (vía `QueryRow` cuando llega desde `QueryProvider.execute_readonly_query`) — features temporales de `Order Date`, categóricas, `Quantity`, y `has_discount` — más, **solo cuando se usa para entrenamiento**, el target `sales`. Es la única representación que cruza el límite `src/mlops/features.py` → `src/mlops/{split,linear_model,catboost_model,evaluation}.py` (Principle I: nada de `dict`/`DataFrame` sin tipar cruza el módulo).

| Field | Type | Notes |
|---|---|---|
| `order_date` | `datetime` | Fecha original de la orden — usada por `split.py` para el corte cronológico. NO se pasa directamente al modelo como columna (se reemplaza por las features temporales derivadas abajo), pero se conserva en el objeto para poder ordenar/particionar. |
| `order_dow` | `int` | Día de la semana derivado de `order_date` (`0=Monday` .. `6=Sunday`, convención `datetime.weekday()`). FR-002. |
| `order_month` | `int` | Mes (`1`-`12`) derivado de `order_date`. FR-002. |
| `order_day_of_month` | `int` | Día del mes (`1`-`31`) derivado de `order_date`. FR-002. |
| `is_weekend` | `bool` | `True` si `order_dow` es sábado o domingo. FR-002. |
| `ship_mode` | `str` | Copiado de `OrderRow.ship_mode`. Baja cardinalidad (one-hot). |
| `segment` | `str` | Copiado de `OrderRow.segment`. Baja cardinalidad (one-hot). |
| `region` | `str` | Copiado de `OrderRow.region`. Baja cardinalidad (one-hot). |
| `market` | `str` | Copiado de `OrderRow.market`. Baja cardinalidad (one-hot). |
| `product_id` | `str` | Copiado de `OrderRow.product_id`. **Alta cardinalidad** (frequency encoding). |
| `sub_category` | `str` | Copiado de `OrderRow.sub_category`. Baja/media cardinalidad (one-hot). |
| `category` | `str` | Copiado de `OrderRow.category`. Baja cardinalidad (one-hot). |
| `quantity` | `int` | Copiado de `OrderRow.quantity`. Feature numérica continua, sin transformación. |
| `has_discount` | `bool` | `Discount > 0` estrictamente. Cualquier `Discount` negativo (anomalía de datos) se trata como `False` y se loguea, sin bloquear el entrenamiento (FR-003, Edge Cases). |
| `sales` | `Decimal \| None` | Target. `None` en `PredictionInput`-derived rows (inferencia); requerido (`not None`) en filas de entrenamiento — validado en `FeatureSet` (ver abajo), no a nivel de campo individual, para que el mismo modelo sirva ambos casos sin duplicar la clase. |

**Validation rules**:
- `order_dow` ∈ `[0, 6]`; `order_month` ∈ `[1, 12]`; `order_day_of_month` ∈ `[1, 31]` (Pydantic `Field(ge=..., le=...)`).
- `quantity >= 0`.
- `sales`, cuando no es `None`, MUST ser `>= 0` (consistente con `OrderRow.sales: Decimal`, que ya es un monto no-negativo por EDA de `001`).
- No incluye `Order ID`/`Row ID` ni ninguna columna no listada en FR-004 (en particular, no incluye `Register`, que no existe en `OrderRow` — ver Assumptions del spec).

### 2. `FeatureSet`

Colección versionada de `SalesFeatureRow` extraída para un run de entrenamiento (o, con `rows` de longitud 1, para una predicción). Producida por `src/mlops/dataset.py`.

| Field | Type | Notes |
|---|---|---|
| `rows` | `list[SalesFeatureRow]` | Ordenadas de forma determinista por `order_date` (ver research.md Part E) antes de hashear/particionar. |
| `data_hash` | `str` | SHA-256 hexdigest sobre la serialización canónica de `rows` (research.md Part E). Calculado una vez por extracción, reutilizado por ambos modelos del mismo run de `train-sales-model`. |
| `extracted_at` | `datetime` | Timestamp UTC de la extracción (metadata, NO participa del hash — mismo patrón que `semantic_layer.json`/`generated_at` de `003`). |
| `source_table` | `Literal["Orders"]` | Documenta la procedencia (siempre `Orders` en v3.0). |
| `row_count` | `int` | `len(rows)` — cacheado para reportes rápidos sin recorrer la lista. |

**Validation rules**:
- `row_count == len(rows)` (invariante verificada en `model_validator`).
- `rows` no puede estar vacío para un `FeatureSet` destinado a entrenamiento (validado en `src/mlops/dataset.py`, no como restricción de campo — un `FeatureSet` de una sola fila es válido para inferencia).
- Todas las filas MUST tener `sales is not None` cuando el `FeatureSet` se usa como input de `training.py` (verificado explícitamente antes de fit, con un error claro si alguna fila careciera del target — no debería ocurrir dado que `Sales` es `NOT NULL` en el schema de `001`, pero se defiende igual).

### 3. `ModelRunMetadata`

Metadata de una corrida de entrenamiento de UN modelo (una instancia por `model_name` por invocación de `train-sales-model` — dos instancias por corrida, una por modelo). Persistida como `params.json` (parcialmente) + usada para construir la entrada del registry.

| Field | Type | Notes |
|---|---|---|
| `run_id` | `str` | Identificador único (p. ej. ULID/UUID4 + timestamp-prefijo para orden natural de listado). Generado en `registry.py` al iniciar la persistencia del run. |
| `model_name` | `Literal["linear_regression", "catboost"]` | FR-013. |
| `hyperparameters` | `dict[str, Union[str, int, float, bool]]` | Hiperparámetros del modelo (p. ej. `{"fit_intercept": true}` para LinearRegression; `{"iterations": 500, "depth": 6, "learning_rate": 0.05}` para CatBoost) MÁS los del split compartido (`test_fraction`, `min_test_rows`). Tipado como `dict` de valores primitivos (no `Any`) porque el conjunto exacto de claves varía por `model_name` — es la única concesión de flexibilidad estructural en este contrato, justificada porque params de hiperparámetros son inherentemente heterogéneos entre familias de modelo, y cada valor individual SÍ está tipado (`str`/`int`/`float`/`bool`, nunca `Any`). |
| `library_versions` | `dict[str, str]` | `{"scikit-learn": "1.9.0"}` o `{"catboost": "1.2.10"}` — versión instalada al momento del fit (vía `importlib.metadata.version(...)`), para trazabilidad (Principle V: "traceable to its source data and code commit"). |
| `data_hash` | `str` | Copiado de `FeatureSet.data_hash` — el mismo para ambos modelos de la misma corrida (FR-009). |
| `trained_at` | `datetime` | Timestamp UTC de inicio del fit. |
| `train_row_count` | `int` | Tamaño del train set tras el split cronológico. |
| `test_row_count` | `int` | Tamaño del test set tras el split cronológico. |
| `split_cutoff_date` | `datetime` | `order_date` de la primera fila del test set — documenta dónde cayó el corte cronológico (FR-013, Edge Cases). |
| `artifact_path` | `str` | Ruta relativa al artifact serializado dentro de `.artifacts/mlops/models/<model_name>/<run_id>/` (p. ej. `model.joblib` o `model.cbm`). |
| `training_duration_ms` | `int` | Tiempo de fit, para el reporte comparado del CLI (US1 AC2). |

**Validation rules**:
- `run_id` MUST ser único dentro del registry (verificado por `registry.py` al persistir, no a nivel de campo — requiere consultar el estado global).
- `train_row_count + test_row_count` MUST ser consistente con el `FeatureSet.row_count` de origen (invariante de `training.py`, no del modelo en sí).

### 4. `EvaluationMetrics`

Métricas de un `ModelRunMetadata` sobre el test set cronológico compartido. Persistida como `metrics.json`.

| Field | Type | Notes |
|---|---|---|
| `rmse` | `float` | Root Mean Squared Error sobre el test set. FR-011. |
| `mae` | `float` | Mean Absolute Error sobre el test set. FR-011. |
| `r2` | `float` | R² (coefficient of determination) sobre el test set. FR-011. Puede ser negativo (peor que predecir la media) — no se restringe a `[0, 1]`. |
| `test_row_count` | `int` | Duplicado de `ModelRunMetadata.test_row_count` para que `metrics.json` sea autocontenido (inspeccionable sin leer `params.json`, FR-015). |
| `split_cutoff_date` | `datetime` | Duplicado de `ModelRunMetadata.split_cutoff_date` — mismo motivo (autocontenido). |

**Validation rules**:
- `rmse >= 0`, `mae >= 0` (propiedades matemáticas de esas métricas).
- `test_row_count > 0` (un `EvaluationMetrics` nunca se construye sobre un test set vacío — `split.py` ya falla antes si el mínimo no se alcanza).

### 5. `ArtifactRegistryEntry`

Una entrada resumida en el listado del registry (lo que produce "listar el artifact registry" de FR-015 / US2 AC3) — vista de solo-lectura combinando `ModelRunMetadata` + `EvaluationMetrics` + estado de promoción, SIN requerir deserializar el modelo.

| Field | Type | Notes |
|---|---|---|
| `run_id` | `str` | — |
| `model_name` | `Literal["linear_regression", "catboost"]` | — |
| `trained_at` | `datetime` | — |
| `metrics` | `EvaluationMetrics` | — |
| `promoted_environments` | `list[Literal["dev", "staging", "prod"]]` | Ambientes donde este `run_id` es ACTUALMENTE el activo (puede ser más de uno, p. ej. promovido a `dev` y `staging` simultáneamente; puede ser vacío si nunca fue promovido o fue reemplazado). Derivado del estado vigente de `registry.json`, no almacenado independientemente. |

**Validation rules**:
- Ninguna adicional más allá de los tipos — es una vista derivada, no una fuente de verdad (la fuente de verdad es `ModelRunMetadata`/`EvaluationMetrics` persistidos por-run + el manifiesto de promoción, ver entidad 6).

### 6. `PromotionRecord`

Un evento de promoción — la unidad atómica del historial que satisface FR-019 ("conservar el historial, no solo el estado vigente").

| Field | Type | Notes |
|---|---|---|
| `environment` | `Literal["dev", "staging", "prod"]` | Ambiente al que se promovió. |
| `run_id` | `str` | El `run_id` promovido. |
| `promoted_at` | `datetime` | Timestamp UTC de la promoción. |
| `bypassed_staging_gate` | `bool` | `True` si esta promoción a `prod` se hizo con el flag explícito de bypass sin pasar por `staging` primero (FR-018) — un evento de gobernanza que queda registrado en el historial, no solo en el log de texto. Default `False`. |

**Validation rules**:
- `bypassed_staging_gate = True` solo es válido cuando `environment == "prod"` (validado por `registry.py` al construir el record, no como restricción de campo aislada — depende de la combinación de campos, `model_validator`).

El manifiesto `registry.json` (`ArtifactRegistryDocument`, el documento top-level que se serializa a disco) contiene:

| Field | Type | Notes |
|---|---|---|
| `version` | `str` | Semver del formato del manifiesto (`1.0.0` inicialmente). |
| `runs` | `list[ArtifactRegistryEntry]` | Todos los runs conocidos, de ambos `model_name`. |
| `promotion_history` | `list[PromotionRecord]` | TODOS los eventos de promoción, de todos los ambientes, en orden cronológico. El "estado vigente" por ambiente se deriva tomando, para cada `environment`, el `PromotionRecord` más reciente — no se almacena como un campo separado duplicado, para evitar que ambas representaciones diverjan. |

### 7. `PredictionInput`

Input tipado para `predict-sales`, equivalente a `SalesFeatureRow` **sin** `sales` (que es el target — nunca se provee en una predicción) — FR-021.

| Field | Type | Notes |
|---|---|---|
| `order_date` | `datetime` | Se deriva a `order_dow`/`order_month`/`order_day_of_month`/`is_weekend` mediante la MISMA función de `src/mlops/features.py` usada en entrenamiento (FR-023 — no una reimplementación paralela). |
| `ship_mode` | `str` | — |
| `segment` | `str` | — |
| `region` | `str` | — |
| `market` | `str` | — |
| `product_id` | `str` | — |
| `sub_category` | `str` | — |
| `category` | `str` | — |
| `quantity` | `int` | `>= 0`. |
| `discount` | `Decimal` | El valor crudo de `Discount` (NO `has_discount` — se deriva internamente con la misma regla `> 0`, para que el caller de `predict-sales` provea el mismo tipo de input "natural" que existiría en una orden real, no un booleano pre-computado). |

**Validation rules**: mismas restricciones de tipo/rango que los campos equivalentes de `SalesFeatureRow`.

### 8. `PredictionResult`

Resultado de una predicción — FR-021/FR-024.

| Field | Type | Notes |
|---|---|---|
| `predicted_sales` | `Decimal` | Predicción numérica de `Sales`. Redondeado a 2 decimales (consistente con `OrderRow.sales: Decimal`, un monto monetario). |
| `run_id` | `str` | El `run_id` del modelo activo usado (el promovido en el ambiente solicitado). |
| `model_name` | `Literal["linear_regression", "catboost"]` | Qué familia de modelo generó la predicción. |
| `environment` | `Literal["dev", "staging", "prod"]` | El ambiente consultado. |
| `used_fallback_encoding` | `bool` | `True` si alguna columna categórica del `PredictionInput` no estaba en el vocabulario de entrenamiento del modelo (research.md Part F). |
| `latency_ms` | `int` | Tiempo total de la predicción (carga del artifact + inferencia), para el log de observabilidad (FR-025). |

**Validation rules**:
- `predicted_sales` puede ser negativo en teoría (un modelo de regresión lineal no está restringido a `>= 0`) — NO se clampa a `0`, para no ocultar señales de un modelo mal calibrado; se documenta como comportamiento esperado, no un bug.

## Relationships

```text
QueryProvider.execute_readonly_query(sql, orders_table_def)
        │  (list[QueryRow], validated read-only SQL — research.md Part G)
        ▼
src/mlops/features.py  ──derives──▶  SalesFeatureRow  (shared training ⇄ inference, FR-023)
        │
        ▼
src/mlops/dataset.py  ──builds & hashes──▶  FeatureSet  (data_hash, research.md Part E)
        │
        ▼
src/mlops/split.py  ──chronological split (research.md Part B)──▶  (train: list[SalesFeatureRow], test: list[SalesFeatureRow])
        │
        ├──▶ src/mlops/linear_model.py  ──fit──▶  sklearn Pipeline  ──▶  src/mlops/evaluation.py  ──▶  EvaluationMetrics
        └──▶ src/mlops/catboost_model.py ──fit──▶  CatBoostRegressor ──▶  src/mlops/evaluation.py  ──▶  EvaluationMetrics
                                                            │
                                                            ▼
                                      ModelRunMetadata + EvaluationMetrics + artifact
                                                            │
                                                            ▼
                              src/mlops/registry.py  ──persists (todo-o-nada, FR-016)──▶  <run_id>/ + registry.json
                                                            │
                                        promote-sales-model │  (PromotionRecord, FR-017..FR-020)
                                                            ▼
                                          registry.json (ArtifactRegistryDocument)
                                                            │
                                          predict-sales ────┘ (resolves active run_id per environment)
                                                            │
                              PredictionInput ──derives (src/mlops/features.py, same code)──▶ SalesFeatureRow (no `sales`)
                                                            │
                              src/mlops/inference.py ──loads artifact + predicts──▶ PredictionResult
```

## Out of scope for this data model

- No se modela un `DriftSignal`/`MonitoringEvent` — explícitamente fuera de alcance de la spec v3.0 (deuda documentada para v3.1+).
- No se modela un `FeatureStoreEntry` — no hay feature store persistente; `FeatureSet` es efímero, recalculado on-demand por cada `train-sales-model`/`predict-sales`.
- No se modela un `HyperparameterSearchSpace`/`TuningResult` — no hay AutoML/HPO en v3.0 (hiperparámetros por defecto documentados, ver `ModelRunMetadata.hyperparameters`).
