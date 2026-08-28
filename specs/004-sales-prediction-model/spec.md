# Feature Specification: Sales Prediction Model (MLOps)

**Feature Branch**: `004-sales-prediction-model`

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "Build a Sales prediction capability that trains and serves regression models (a baseline scikit-learn Linear Regression and a CatBoost Regressor) to predict Sales for the Orders table (Global Superstore data) already loaded in the Postgres warehouse. Train on Order Date-derived time features, Ship Mode, Segment, City, State, Country, Region, Market, Product ID, Product Name, Sub-Category, Category, Quantity and a has_discount boolean; evaluate with a chronological (non-random) train/test split and RMSE/MAE/R²; compare a scikit-learn LinearRegression pipeline against a CatBoostRegressor with native categorical handling; satisfy constitution Principle V (reproducible, tracked, staged-promotion MLOps) and Principle II (new isolated src/mlops/ domain, reads only via QueryProvider contracts) and Principle I (strict typing); add CLI commands train-sales-model and predict-sales; add scikit-learn and catboost to pyproject.toml."

## Amendment (2026-08-25): Feature-set reduction based on `data_dictionary.md`

Post-implementation review of the requested feature list against the documented
column cardinalities in [`data_dictionary.md`](../../data_dictionary.md)
(EDA-verified, 51290-row `Orders`) found four columns with **no defensible
predictive value** for `Sales` once `Region`/`Market`/`Product ID` are already
features, and DROPPED them from the trained feature set (code + this spec):

- **`City`** (3650 unique values) and **`State`** (1106 unique values): this
  dataset has no city/state-level price localization (not documented anywhere
  in the data dictionary); both are redundant, much-higher-cardinality
  restatements of the same geography already captured by `Region` (23) /
  `Market` (5), which are kept. Including them would only inflate encoding
  dimensionality and dilute per-category sample counts.
- **`Country`** (165 unique values): redundant nested geography, same
  rationale as City/State — `Region`/`Market` already generalize it.
- **`Product Name`** (3788 unique values): the data dictionary shows this is
  **exactly** as cardinal as `Product ID` (3788 unique values each) — a
  bijective, purely redundant free-text duplicate of the same identifier.
  `Product ID` is kept as the canonical form; `Product Name` adds zero
  incremental signal while doubling encoding cost.

`Quantity` and `has_discount` (derived from `Discount`) were re-confirmed as
legitimate, order-time-known predictors (not derived FROM `Sales`, unlike
`Profit`/`Shipping Cost`, which remain excluded to avoid label leakage).

**Empirical confirmation**: retraining after the removal changed RMSE by
<2% for both models (`linear_regression` 399.31→399.43, `catboost`
274.09→279.83), confirming the dropped columns carried negligible signal.

The final feature set is: temporal features derived from `Order Date`,
`Ship Mode`, `Segment`, `Region`, `Market`, `Product ID`, `Sub-Category`,
`Category`, `Quantity`, `has_discount`. All FRs/ACs below are updated to
reflect this; `src/mlops/features.py` carries the same rationale as a
module docstring.

## Amendment (2026-08-26): Persist prediction history to a SQL `Predictions` table

Added FR-025a: in addition to the `.artifacts/mlops/predict_sales.log` JSONL
append-log (FR-025, unchanged), every `predict-sales` call now also inserts
one row into a `Predictions` SQL table — the predicted `Sales` value, the
date/hour the prediction was made, the `run_id`/`model_name`/`environment`
that served it, and every input parameter used to predict. This makes the
prediction history queryable (e.g. `SELECT * FROM "Predictions"`) instead of
only inspectable via the log file. New module `src/mlops/predictions_store.py`
builds the table definition and row, and persists through the existing
engine-neutral `SchemaProvider`/`DataProvider` Protocols — no new coupling to
Postgres internals inside `src/mlops/` (FR-026 unaffected). The `bootstrap`
CLI command now also creates the (initially empty) `Predictions` table
alongside `Orders`/`Returns`/`People`. See `data-model.md` § 9 and
`contracts/mlops_inference.md` for the full contract.

## Scope Summary

Esta especificación define el milestone **v3.0 (MLOps)** de la Plataforma de Datos y GenAI: la primera capacidad del dominio **MLOps** (constitución, Principle II), separado e independiente de Data Engineering (`001-data-genai-platform-baseline`) y AI Engineering (`002-text-to-sql-v1`, `003-semantic-layer-v1`).

El objetivo es entrenar, evaluar, versionar y servir un modelo de **predicción de `Sales`** (regresión) para la tabla `Orders` (Global Superstore), comparando dos enfoques:

1. **Baseline interpretable**: `scikit-learn` `LinearRegression` dentro de un `Pipeline`/`ColumnTransformer` con encoding explícito por tipo de columna (one-hot para categóricas de baja cardinalidad; una estrategia distinta y documentada para las de muy alta cardinalidad como `Product ID`, la única de muy alta cardinalidad retenida — ver Amendment arriba).
2. **Modelo de mayor capacidad**: `CatBoostRegressor`, que maneja categóricas nativamente vía `cat_features` sin encoding manual, incluyendo las de alta cardinalidad.

Ambos modelos se entrenan sobre el **mismo split cronológico** (train = órdenes más antiguas, test = las más recientes, partidos por `Order Date`) — nunca un split aleatorio, porque `Order Date` es en sí una feature y un split aleatorio filtraría información temporal (leakage). Se evalúan con las mismas métricas (RMSE, MAE, R²) sobre el mismo test set para que la comparación sea justa.

Por constitución (Principle V, Reproducible MLOps), cada corrida de entrenamiento debe ser **versionada y trazable** (artifact del modelo + hiperparámetros + métricas + hash de los datos fuente), el **experiment tracking es obligatorio** (no opcional/best-effort), y los modelos deben poder **promoverse por ambientes** (dev → staging → prod) con un mecanismo mínimo viable apropiado para un entorno local Docker/Postgres-only (sin infraestructura cloud nueva): un **registro de artifacts basado en archivos** (directorio versionado en disco, p. ej. `.artifacts/mlops/models/<model_name>/<run_id>/`) más un **comando CLI `promote-sales-model`** que mueve/marca un `run_id` como el modelo activo de un ambiente dado (`dev`/`staging`/`prod`), registrado en un manifiesto (`registry.json`) versionado junto a los artifacts.

Por constitución (Principle II, Layered Separation of Concerns), el nuevo dominio vive en `src/mlops/`, aislado de `data_engineering` y `ai_engineering` internals, y **NUNCA** accede a Postgres directamente (sin `psycopg`/SQL crudo dentro de `mlops`): toda lectura de datos de entrenamiento pasa por el `QueryProvider` (contrato existente en `src/data_access/interfaces.py`), igual que lo hace `ai_engineering` hoy.

Por constitución (Principle I), todos los contratos de entrada/salida del dominio MLOps (features de entrenamiento, resultado de evaluación, metadata de un run, entrada de predicción) son modelos Pydantic v2 (`frozen`) o `dataclasses` congelados, con type hints completos y `mypy --strict` limpio.

### Entregables concretos (v3.0 — MLOps)

- Un módulo `src/mlops/` nuevo, con:
  - **Feature engineering** (derivación determinista de features desde `OrderRow` vía `QueryProvider`: features temporales de `Order Date` — día de semana, mes, `is_weekend`, día del mes —, y `has_discount = Discount > 0`).
  - **Training pipeline** para ambos modelos (`LinearRegression` en `Pipeline`/`ColumnTransformer`; `CatBoostRegressor` con `cat_features` nativo).
  - **Split cronológico** reutilizable (misma función/cutoff para ambos modelos).
  - **Evaluación** (RMSE, MAE, R² sobre el mismo test set, para ambos modelos).
  - **Experiment tracking / artifact registry** basado en archivos: cada run persiste hiperparámetros, métricas, hash del dataset de entrenamiento (fuente + fecha de extracción), y el artifact serializado del modelo, bajo un `run_id` único.
  - **Promoción por ambiente** (`dev`/`staging`/`prod`) vía manifiesto `registry.json` y comando CLI dedicado.
  - **Inferencia** sobre un modelo promovido a un ambiente dado, a partir de inputs tipados.
- **Comandos CLI** (siguiendo el patrón de `src/cli/main.py`): `train-sales-model` (entrena ambos modelos, imprime/loguea métricas comparadas, persiste ambos runs), `promote-sales-model` (promueve un `run_id` a un ambiente), y `predict-sales` (carga el modelo activo de un ambiente y predice para inputs nuevos).
- **Nuevas dependencias**: `scikit-learn` y `catboost` agregadas a `pyproject.toml`.
- **Tests de contrato** para los límites de dominio (features tipadas, resultado de evaluación tipado, no-bypass de `QueryProvider`) y **tests de reproducibilidad** (misma data + mismos hiperparámetros ⇒ mismas métricas).

### Explicitly out of scope

- **Serving HTTP/API en tiempo real** (endpoint REST/gRPC para `predict-sales`) — v3.0 entrega inferencia batch/CLI únicamente sobre inputs provistos localmente; un servicio de inferencia expuesto queda para una iteración futura.
- **Monitoreo de drift en producción y alerting automatizado** — la constitución (Principle V) exige que la inferencia en producción sea "observable" a futuro; v3.0 deja logueados los inputs/outputs/latencia de cada `predict-sales` (governance-aware) pero NO implementa detección de drift ni alertas — se documenta como deuda explícita para v3.1+.
- **Feature store centralizado / features online** — las features se derivan on-demand desde `QueryProvider` en cada training run; no hay un feature store persistente ni cálculo incremental.
- **Tuning automático de hiperparámetros (AutoML/HPO)** — ambos modelos usan hiperparámetros razonables por defecto (documentados y versionados), no hay grid/random/bayesian search en v3.0.
- **Nuevos modelos más allá de los dos especificados** (sin ensambles, sin redes neuronales, sin series temporales tipo ARIMA/Prophet).
- **Infraestructura cloud nueva** (MLflow server, S3/GCS, model registry gestionado) — el artifact registry es un directorio versionado local/Docker-mountable; migrar a un backend gestionado es explícitamente un paso futuro y NO bloquea esta feature.
- **Reentrenamiento automático programado (scheduling/cron/orquestador)** — el entrenamiento se dispara manualmente vía CLI; no hay pipeline de reentrenamiento automático en v3.0.
- **Resolución de "Register" como feature categórica dedicada** — ver Assumptions: no existe una columna `Register`/`Registro` en `Orders` (ver `OrderRow` en `src/contracts/data_access.py`); se documenta como asunción y se excluye de las features de entrenamiento (ver más abajo).
- **BigQuery migration** — fuera de alcance; PostgreSQL local solo, vía el mismo `QueryProvider` engine-agnostic ya existente.

### Roadmap context

| Hito | Estado | Feature |
| --- | --- | --- |
| M0 | Completado | `001-data-genai-platform-baseline` (warehouse + dictionary) |
| M1–M2 | Completado | `002-text-to-sql-v1` (Text-to-SQL v1.0/v1.1) |
| M3 | Completado | `003-semantic-layer-v1` (Semantic Layer v2.0, RLS por Region) |
| **M-MLOps** | **Propuesto** | **`004-sales-prediction-model` (esta feature, v3.0 — primera entrega del dominio MLOps)** |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Entrenar y comparar los dos modelos de predicción de Sales (Priority: P1) 🎯 MVP

Un Data Scientist / MLOps Engineer necesita ejecutar un único comando que entrene ambos modelos (`LinearRegression` baseline y `CatBoostRegressor`) sobre los datos actuales de `Orders`, usando un split cronológico, y obtener un reporte comparado de métricas (RMSE, MAE, R²) para decidir cuál modelo es mejor candidato.

**Why this priority**: Sin esta capacidad no existe el dominio MLOps ni ningún modelo entrenado; es el cimiento de todo lo demás (promoción, predicción). Entrega valor independiente: aunque nada se promoviera a producción todavía, ya permite comparar enfoques y tomar una decisión informada.

**Independent Test**: Con el warehouse cargado (`Orders` poblada), ejecutar `train-sales-model` y verificar que produce dos runs persistidos (uno por modelo) con métricas (RMSE, MAE, R²) sobre el mismo test set cronológico, y que el CLI imprime una tabla comparativa legible.

**Acceptance Scenarios**:

1. **Given** el warehouse Postgres corriendo con `Orders` cargada, **When** se ejecuta `train-sales-model`, **Then** el sistema deriva las features (temporales de `Order Date`, `has_discount`, categóricas, `Quantity`), separa train/test cronológicamente (train = fechas más antiguas, test = las más recientes, sin shuffle), entrena `LinearRegression` (en `Pipeline`/`ColumnTransformer`) y `CatBoostRegressor` (con `cat_features` nativo), y calcula RMSE/MAE/R² de ambos sobre el mismo test set.
2. **Given** el entrenamiento completado, **When** se inspecciona la salida del CLI, **Then** se muestra una comparación lado a lado de ambos modelos (RMSE, MAE, R², tiempo de entrenamiento) y cuál obtuvo mejor RMSE.
3. **Given** dos ejecuciones consecutivas de `train-sales-model` sobre el mismo estado de datos y los mismos hiperparámetros, **When** se comparan los `run_id` resultantes, **Then** las métricas reportadas son idénticas (mismo hash de datos fuente, mismos hiperparámetros, mismo split) — reproducibilidad verificada.
4. **Given** la columna de altísima cardinalidad `Product ID` (única retenida tras el Amendment — ver arriba; `Product Name`/`City`/`State`/`Country` fueron descartadas), **When** se construye el pipeline de `LinearRegression`, **Then** el sistema NO aplica one-hot directo sobre ella (evita explosión dimensional) sino una estrategia explícita y documentada (p. ej. target/frequency encoding o agrupamiento en categorías infrecuentes → "other"), mientras que `CatBoostRegressor` la recibe vía `cat_features` sin encoding manual.
5. **Given** que la columna `Register` solicitada en la descripción original no existe literalmente en `Orders`, **When** se construye el conjunto de features, **Then** el sistema la omite y documenta la ausencia (ver Assumptions) sin fallar el entrenamiento.

---

### User Story 2 - Versionar y rastrear cada corrida de entrenamiento (Priority: P1)

Un MLOps Engineer necesita que cada corrida de entrenamiento quede versionada y sea auditable: qué datos se usaron (hash), qué hiperparámetros, qué métricas resultaron, y dónde está el artifact del modelo — sin depender de memoria humana ni de nombres de archivo ad-hoc.

**Why this priority**: Es el requisito NON-NEGOTIABLE-adjacente de la constitución (Principle V: "every model... MUST be versioned and traceable"; "experiment tracking is MANDATORY"). Sin esto, `train-sales-model` de US1 produciría modelos no auditables ni promovibles con confianza. P1 porque bloquea US3 (promoción) y cualquier reclamo de cumplimiento constitucional.

**Independent Test**: Ejecutar `train-sales-model` dos veces con datos o hiperparámetros distintos; inspeccionar el artifact registry y confirmar dos `run_id` distintos, cada uno con su propio directorio conteniendo: hiperparámetros (json), métricas (json), hash del dataset fuente, y el artifact del modelo serializado.

**Acceptance Scenarios**:

1. **Given** una corrida de `train-sales-model`, **When** finaliza, **Then** se persiste un directorio `run_id` único (p. ej. `.artifacts/mlops/models/<model_name>/<run_id>/`) conteniendo: `params.json` (hiperparámetros + versión de librería), `metrics.json` (RMSE/MAE/R², tamaño de train/test, fecha del corte cronológico), `data_hash.txt` (hash determinista de los datos de entrenamiento extraídos vía `QueryProvider`), y el artifact del modelo serializado (formato apropiado por librería).
2. **Given** dos runs con el mismo `data_hash` y los mismos hiperparámetros, **When** se comparan sus `metrics.json`, **Then** son idénticos (reproducibilidad).
3. **Given** un run persistido, **When** se lista el artifact registry, **Then** cada entrada es identificable por `model_name`, `run_id`, timestamp, y estado (`untracked`/promovido a algún ambiente).
4. **Given** que Postgres no está disponible, **When** se ejecuta `train-sales-model`, **Then** falla rápido con un error claro (sin producir un run parcial/corrupto en el registry).

---

### User Story 3 - Promover un modelo entrenado a un ambiente (dev → staging → prod) (Priority: P2)

Un MLOps Engineer, tras revisar las métricas de un run, quiere marcarlo explícitamente como el modelo activo de un ambiente (`dev`, `staging` o `prod`), de forma que `predict-sales` en ese ambiente siempre use el `run_id` promovido — sin despliegue directo a producción sin pasos intermedios.

**Why this priority**: P2 — se construye sobre US2 (un run debe existir y estar versionado antes de poder promoverse). Es necesario para satisfacer el requisito constitucional de "staged environments... no direct-to-prod deployment", pero no es el MVP por sí solo (no aporta valor sin US1/US2).

**Independent Test**: Entrenar un modelo (US1), promoverlo a `staging` con `promote-sales-model --run-id <id> --env staging`, y verificar que `registry.json` refleja `staging → <run_id>` y que un intento de promover directamente a `prod` sin pasar antes por `staging` es rechazado (o exige `--force` con warning de gobernanza, documentado explícitamente).

**Acceptance Scenarios**:

1. **Given** un `run_id` existente en el registry, **When** se ejecuta `promote-sales-model --run-id <id> --env staging`, **Then** `registry.json` se actualiza para que `staging` apunte a ese `run_id`, con timestamp de promoción.
2. **Given** un `run_id` que nunca fue promovido a `staging`, **When** se intenta `promote-sales-model --run-id <id> --env prod`, **Then** el sistema rechaza la promoción directa a `prod` con un mensaje claro indicando que debe pasar primero por `staging` (a menos que se pase un flag explícito de bypass, registrado como evento de gobernanza en el log).
3. **Given** un `run_id` inexistente, **When** se intenta promoverlo a cualquier ambiente, **Then** falla rápido listando los `run_id` disponibles.
4. **Given** un ambiente con un modelo ya promovido, **When** se promueve un nuevo `run_id` al mismo ambiente, **Then** el registry conserva el historial (no solo el estado actual) — se puede auditar qué modelo estuvo activo antes.

---

### User Story 4 - Predecir Sales usando el modelo promovido de un ambiente (Priority: P2)

Un usuario (analista, otro sistema, o el propio MLOps Engineer validando el modelo) quiere obtener una predicción de `Sales` para una o más órdenes hipotéticas/nuevas, usando el modelo actualmente promovido en un ambiente dado, sin necesidad de conocer detalles de implementación del modelo.

**Why this priority**: P2 — depende de US1 (modelo entrenado) y US3 (modelo promovido); es la superficie de consumo que hace el resto útil, pero no es el MVP aislado.

**Independent Test**: Con un `run_id` promovido a `dev`, ejecutar `predict-sales --env dev` con un input tipado (features de una orden hipotética) y verificar que devuelve una predicción numérica de `Sales` junto con metadata del modelo usado (`run_id`, nombre del modelo).

**Acceptance Scenarios**:

1. **Given** un modelo promovido a un ambiente, **When** se ejecuta `predict-sales --env <env>` con un input válido (Ship Mode, Segment, Region, Market, Product ID, Sub-Category, Category, Quantity, Discount, Order Date), **Then** el sistema deriva las mismas features usadas en entrenamiento (mismo código de feature engineering, no duplicado) y devuelve una predicción numérica de `Sales` más el `run_id`/modelo usado.
2. **Given** un ambiente sin ningún modelo promovido, **When** se ejecuta `predict-sales --env <env>`, **Then** falla rápido con un error claro indicando que no hay modelo activo en ese ambiente.
3. **Given** un input con una categoría nunca vista en entrenamiento (p. ej. un `Product ID` nuevo), **When** se predice, **Then** el sistema no falla — cae a la estrategia de "categoría desconocida" definida en el pipeline (p. ej. bucket "other"/valor por defecto de CatBoost) y lo señala en la respuesta (p. ej. `used_fallback_encoding: true`).
4. **Given** que `predict-sales` se ejecuta, **When** finaliza, **Then** el input (sujeto a gobernanza) y la predicción quedan logueados junto con latencia, preparando el terreno para observabilidad de producción futura (Principle V).

---

### Edge Cases

- **¿Qué pasa si `Orders` tiene muy pocas filas recientes (test set cronológico demasiado pequeño)?** El sistema aplica un tamaño mínimo de test set configurable (p. ej. últimos N% de fechas o un mínimo de filas); si no se alcanza, `train-sales-model` falla rápido con un mensaje explicando el requisito, en lugar de entrenar sobre un test set no representativo.
- **¿Qué pasa con valores de `Discount` exactamente 0 vs valores negativos (si existieran)?** `has_discount = Discount > 0` (estrictamente mayor a cero); cualquier valor negativo (inesperado según EDA de `001`) se trata como `has_discount = False` y se loguea como anomalía de datos, sin bloquear el entrenamiento.
- **¿Qué pasa si `Order Date` tiene huecos o el corte cronológico cae en un fin de semana/feriado?** El corte se define por proporción de filas ordenadas por fecha (p. ej. últimas N% filas por fecha), no por una fecha calendario fija, evitando splits vacíos.
- **¿Qué pasa si una columna categórica de alta cardinalidad tiene valores nuevos en inferencia que no existían en entrenamiento?** Ver User Story 4, Acceptance Scenario 3 — fallback explícito y señalizado, nunca una excepción no controlada.
- **¿Qué pasa si se intenta `predict-sales` mientras `train-sales-model` está corriendo?** Cada operación opera sobre artifacts inmutables por `run_id`; `predict-sales` siempre lee el `run_id` actualmente promovido en `registry.json` en el momento de la llamada — no hay locking especial requerido porque los runs nunca se sobrescriben in-place.
- **¿Qué pasa si CatBoost y LinearRegression producen RMSE prácticamente empatados?** El CLI reporta ambos igualmente y dominios humanos deciden cuál promover; el sistema no auto-promueve el "ganador" (la promoción siempre es una acción explícita, per Principle V "approval gates").
- **¿Qué pasa si falta `scikit-learn`/`catboost` en el entorno?** `train-sales-model` falla rápido con un mensaje claro indicando la dependencia faltante y cómo instalarla (`uv sync`), sin traceback críptico.

## Requirements *(mandatory)*

### Functional Requirements

#### Data Access & Feature Engineering (US1)

- **FR-001**: El sistema MUST leer los datos de entrenamiento (filas de `Orders`) EXCLUSIVAMENTE a través del `QueryProvider` (contrato existente en `src/data_access/interfaces.py`); ningún módulo bajo `src/mlops/` MUST importar `psycopg`/drivers de base de datos ni construir SQL crudo directamente.
- **FR-002**: El sistema MUST derivar, para cada orden, features temporales a partir de `Order Date`: día de la semana, mes, `is_weekend` (booleano), y día del mes.
- **FR-003**: El sistema MUST derivar `has_discount: bool = Discount > 0` a partir de la columna `Discount` existente.
- **FR-004**: El conjunto de features de entrada al modelo MUST incluir: features temporales (FR-002), `Ship Mode`, `Segment`, `Region`, `Market`, `Product ID`, `Sub-Category`, `Category`, `Quantity`, `has_discount` (FR-003). `City`, `State`, `Country`, y `Product Name` MUST NOT usarse como features (ver Amendment: redundantes con `Region`/`Market`/`Product ID` según cardinalidades de `data_dictionary.md`, sin señal predictiva incremental confirmada empíricamente). La variable objetivo (target) MUST ser `Sales`.
- **FR-005**: El sistema MUST representar el conjunto de features de entrenamiento/inferencia como un modelo tipado (Pydantic v2 `frozen` o `dataclass` congelado) — no como `dict`/`DataFrame` sin tipar cruzando límites de módulo (Principle I).

#### Train/Test Split & Training (US1)

- **FR-006**: El sistema MUST particionar train/test de forma **cronológica** por `Order Date` (train = observaciones más antiguas, test = las más recientes) — un split aleatorio/shuffle MUST NOT usarse.
- **FR-007**: El sistema MUST entrenar un modelo `LinearRegression` (scikit-learn) dentro de un `Pipeline`/`ColumnTransformer` que aplique una estrategia de encoding explícita y documentada por tipo de columna: one-hot (u equivalente) para categóricas de baja/media cardinalidad, y una estrategia distinta para la de muy alta cardinalidad (`Product ID`) que evite explosión dimensional (p. ej. frequency/target encoding, o agrupamiento de categorías infrecuentes).
- **FR-008**: El sistema MUST entrenar un `CatBoostRegressor` que reciba las columnas categóricas nativamente vía el parámetro `cat_features`, sin encoding manual previo.
- **FR-009**: Ambos modelos (FR-007, FR-008) MUST entrenarse sobre el mismo split cronológico (mismo corte, mismas filas de train/test) para que la comparación de métricas sea válida.
- **FR-010**: El sistema MUST exponer un comando CLI `train-sales-model` que ejecuta el flujo completo (features → split → entrenamiento de ambos modelos → evaluación → persistencia de ambos runs) en una sola invocación.

#### Evaluation (US1)

- **FR-011**: El sistema MUST calcular, para cada modelo entrenado, sobre el mismo test set cronológico: RMSE, MAE, y R².
- **FR-012**: El sistema MUST presentar (CLI output) una comparación lado a lado de las métricas de ambos modelos, identificando cuál obtuvo mejor RMSE.

#### Reproducible MLOps: Tracking & Versioning (US2 — Principle V)

- **FR-013**: Cada corrida de entrenamiento MUST generar un `run_id` único y persistir, bajo un directorio versionado del artifact registry: los hiperparámetros usados, las métricas resultantes (FR-011), un hash determinista de los datos de entrenamiento extraídos, y el artifact serializado del modelo.
- **FR-014**: El sistema MUST permitir reproducir un run: dado el mismo hash de datos y los mismos hiperparámetros, una nueva corrida MUST producir métricas idénticas.
- **FR-015**: El artifact registry MUST ser inspeccionable (listado de runs con `model_name`, `run_id`, timestamp, métricas resumidas, estado de promoción) sin requerir deserializar el modelo.
- **FR-016**: Un run fallido/parcial MUST NOT dejar entradas corruptas o incompletas visibles en el registry (todo-o-nada por run).

#### Staged Promotion (US3 — Principle V)

- **FR-017**: El sistema MUST soportar la promoción explícita de un `run_id` existente a uno de los ambientes `dev`, `staging`, `prod`, vía comando CLI `promote-sales-model`.
- **FR-018**: El sistema MUST rechazar la promoción directa de un `run_id` a `prod` si ese mismo `run_id` no fue previamente promovido a `staging`, salvo que se invoque con un flag explícito de bypass, en cuyo caso el bypass MUST quedar registrado como evento de gobernanza en el log.
- **FR-019**: El manifiesto del registry (`registry.json` o equivalente) MUST conservar el historial de promociones por ambiente (no solo el estado vigente), incluyendo timestamp de cada promoción.
- **FR-020**: Una promoción a un `run_id` inexistente MUST fallar rápido, listando los `run_id` disponibles.

#### Inference (US4)

- **FR-021**: El sistema MUST exponer un comando CLI `predict-sales --env <dev|staging|prod>` que carga el modelo actualmente promovido en el ambiente indicado y produce una predicción de `Sales` a partir de un input tipado equivalente al conjunto de features de FR-004 (sin `Sales`, que es el target).
- **FR-022**: `predict-sales` sobre un ambiente sin modelo promovido MUST fallar rápido con un mensaje claro.
- **FR-023**: `predict-sales` MUST reutilizar el mismo código de derivación de features que el entrenamiento (FR-002/FR-003) — no una reimplementación paralela — para evitar train/serve skew.
- **FR-024**: Ante un valor categórico no visto en entrenamiento, `predict-sales` MUST NOT lanzar una excepción no controlada; MUST aplicar la estrategia de fallback definida en el pipeline (FR-007/FR-008) y señalizarlo en la respuesta.
- **FR-025**: Cada invocación de `predict-sales` MUST loguear (sujeto a las reglas de gobernanza de datos existentes) el input, la predicción, el `run_id`/modelo usado, y la latencia — sentando la base para observabilidad de producción exigida por Principle V.
- **FR-025a** *(Amendment 2026-08-26)*: Además del log JSONL de FR-025, cada invocación de `predict-sales` MUST persistir la misma información (predicción, fecha/hora de la predicción, `run_id`/modelo/ambiente, y todos los parámetros de input usados) como una fila en una tabla SQL `Predictions`, creada de forma idempotente (`CREATE TABLE IF NOT EXISTS`) vía los Protocols engine-neutral existentes (`SchemaProvider`/`DataProvider`, `src/data_access/interfaces.py`) — sin acoplar `src/mlops/` a `psycopg` (FR-026 sigue aplicando). Esta persistencia es best-effort: si Postgres no está disponible, `predict-sales` MUST seguir funcionando (solo con el log JSONL), preservando la garantía de FR-021/US4 de que la inferencia no depende de una conexión a base de datos activa.

#### Domain Isolation & Typing (Principle I & II)

- **FR-026**: El nuevo código MUST residir en un módulo `src/mlops/` aislado; MUST NOT importar internals de `src/data_engineering/` ni de `src/ai_engineering/` (solo puede depender de `src/contracts/` y `src/data_access/interfaces.py`, igual que `ai_engineering` hoy).
- **FR-027**: Todos los contratos de entrada/salida del dominio MLOps (conjunto de features, resultado de evaluación, metadata de un run, input de predicción, resultado de predicción) MUST ser modelos Pydantic v2 `frozen` o `dataclasses` congelados, con type hints completos en cada función/método público y privado.
- **FR-028**: El código nuevo MUST pasar `mypy --strict` sin errores, siguiendo la configuración ya presente en `pyproject.toml`.
- **FR-029**: `scikit-learn` y `catboost` MUST agregarse como dependencias declaradas (con versión mínima pineada) en `pyproject.toml`.

### Key Entities *(include if feature involves data)*

- **SalesFeatureRow**: Representación tipada de una fila lista para entrenar/predecir — deriva de `OrderRow` (features temporales de `Order Date`, categóricas, `Quantity`, `has_discount`) más, solo en entrenamiento, el target `Sales`. No incluye `Register` (ver Assumptions).
- **TrainingRun**: Metadata de una corrida de entrenamiento — `run_id`, `model_name` (`linear_regression` | `catboost`), hiperparámetros, `data_hash`, timestamp, ruta del artifact serializado, tamaño de train/test.
- **EvaluationResult**: Métricas de un `TrainingRun` sobre el test set cronológico — RMSE, MAE, R², tamaño de test, fecha de corte del split.
- **ModelRegistryEntry / PromotionRecord**: Estado y decisiones de promoción — ambiente (`dev`/`staging`/`prod`), `run_id` activo, historial de promociones anteriores con timestamp y actor.
- **PredictionRequest / PredictionResponse**: Input tipado equivalente a `SalesFeatureRow` sin `Sales` (request) y predicción numérica + metadata del modelo usado + flag de fallback de categoría desconocida (response).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un Data Scientist puede ejecutar `train-sales-model` end-to-end (desde el warehouse Postgres local hasta un reporte de métricas comparado) en una sola invocación de CLI, sin pasos manuales intermedios.
- **SC-002**: El `CatBoostRegressor` iguala o mejora el RMSE del baseline `LinearRegression` sobre el mismo test set cronológico en al menos el 90% de las corridas de referencia documentadas (dado que maneja categóricas de alta cardinalidad nativamente).
- **SC-003**: Dos corridas de entrenamiento con el mismo `data_hash` y los mismos hiperparámetros producen métricas (RMSE/MAE/R²) idénticas el 100% de las veces (reproducibilidad verificable).
- **SC-004**: El 100% de los runs de entrenamiento quedan versionados en el artifact registry con hiperparámetros, métricas y hash de datos consultables sin deserializar el modelo.
- **SC-005**: Ningún `run_id` puede promoverse a `prod` sin haber pasado antes por `staging`, salvo bypass explícito y registrado — verificado en el 100% de los intentos de promoción directa en pruebas.
- **SC-006**: `predict-sales` devuelve una predicción para un input válido en menos de 2 segundos en el entorno de desarrollo local (experiencia interactiva desde CLI).
- **SC-007**: Un input de predicción con una categoría no vista en entrenamiento nunca produce una excepción no controlada — se degrada de forma explícita el 100% de las veces en pruebas dirigidas.
- **SC-008**: El 100% del código nuevo bajo `src/mlops/` pasa `mypy --strict` sin errores y no contiene ninguna importación directa de un driver de base de datos ni de internals de `data_engineering`/`ai_engineering`.

## Assumptions

- **"Register" no existe como columna en `Orders`**: se revisó `OrderRow` (`src/contracts/data_access.py`, feature `001-data-genai-platform-baseline`) y no hay ninguna columna `Register`/`Registro`. Se asume que la intención original se refería a `Order ID` (identificador de la orden) — que, precisamente por ser un identificador único/casi único, **no se incluye como feature de entrenamiento** (agregaría ruido de altísima cardinalidad sin señal predictiva real y arriesgaría fuga de identidad). Esta ausencia se documenta aquí en vez de bloquear la especificación con una pregunta de clarificación, dado que el resto del conjunto de features (14 columnas) es suficiente para un modelo de regresión razonable y el propio pedido original marcó esto como "open question/assumption" aceptable.
- **El artifact registry es un directorio local versionado (no un servicio gestionado)**: dado el mandato constitucional de "no new cloud infra" y que el entorno de desarrollo es Docker/Postgres-only, se asume que un registry basado en archivos (`.artifacts/mlops/`) más un manifiesto `registry.json` es la implementación mínima viable que satisface "staged promotion" (Principle V) sin introducir un servidor MLflow, S3/GCS, u otro backend gestionado. Migrar a un backend gestionado queda como evolución futura explícita, no como bloqueante de esta feature.
- **`has_discount` reemplaza a `Discount` como feature**: se asume, según lo pedido explícitamente, que el modelo usa el booleano derivado `has_discount = Discount > 0` en lugar del valor continuo de `Discount`, para evitar que el modelo aprenda una relación mecánica (Discount y Sales suelen estar correlacionados por construcción del dataset) y enfocar la predicción en las demás features de negocio.
- **El corte cronológico del split train/test es por proporción de filas ordenadas por `Order Date`** (p. ej. últimas ~20% de las filas por fecha como test), no una fecha calendario fija, para evitar splits degenerados si la distribución de fechas es desigual. El porcentaje exacto es un hiperparámetro versionado por run (no hardcodeado sin registro).
- **Ambos modelos usan hiperparámetros por defecto razonables documentados en el run** (sin búsqueda automática de hiperparámetros) — ver Explicitly out of scope; esto es suficiente para el objetivo de esta feature (comparar dos enfoques y establecer el ciclo MLOps reproducible), no para maximizar performance absoluta.
- **La promoción y el registry son locales al proceso/filesystem del entorno de desarrollo** — no hay control de concurrencia distribuido ni multi-usuario en v3.0; se asume un solo operador/proceso a la vez, consistente con el resto del proyecto (single-tenant, sin autenticación real, per `003-semantic-layer-v1`).
- **No hay autenticación/autorización real para `promote-sales-model`** (consistente con el resto del proyecto, que tampoco tiene login/sesiones) — el flag de bypass de "staging antes de prod" (FR-018) es un control de proceso, no de identidad, y su uso queda registrado en el log como evento de gobernanza.
