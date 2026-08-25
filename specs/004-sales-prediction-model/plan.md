# Implementation Plan: Sales Prediction Model (MLOps v3.0)

**Branch**: `004-sales-prediction-model` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-sales-prediction-model/spec.md`
**Related**: [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md)

> **Amendment (2026-08-25)**: `Product Name`/`City`/`State`/`Country` were
> dropped from the feature set after review — see `spec.md` § Amendment. Only
> `Product ID` remains as the high-cardinality field; all other mentions of
> those four columns below reflect the original (superseded) design.

## Summary

El milestone **v3.0** entrega la primera capacidad del dominio **MLOps**: un módulo nuevo y aislado `src/mlops/` que entrena, evalúa, versiona y sirve un modelo de **predicción de `Sales`** para `Orders` (Global Superstore), comparando un baseline `scikit-learn` `LinearRegression` (dentro de un `Pipeline`/`ColumnTransformer` con encoding explícito por cardinalidad) contra un `CatBoostRegressor` (categóricas nativas vía `cat_features`). Ambos modelos comparten el mismo split cronológico por `Order Date` (nunca aleatorio) y se evalúan con las mismas métricas (RMSE, MAE, R²) sobre el mismo test set.

El approach técnico es deliberadamente minimalista y reutiliza lo que ya existe en vez de inventar infraestructura nueva:

- **Lectura de datos**: `src/mlops/` NUNCA toca Postgres directamente; lee `Orders` exclusivamente vía el `QueryProvider` Protocol existente (`src/data_access/interfaces.py`), igual que `ai_engineering` hoy. Se reutiliza `execute_readonly_query` con un SQL literal de solo-lectura (`SELECT * FROM "Orders"`), validado con el `SqlValidator` existente de `002` como defensa en profundidad, aunque no hay LLM involucrado en esta feature.
- **Feature engineering**: una única función determinista (`src/mlops/features.py`) deriva `SalesFeatureRow` desde cada `OrderRow`/`QueryRow`; se reutiliza literalmente entre entrenamiento e inferencia (no hay reimplementación paralela → evita train/serve skew, FR-023).
- **Encoding de alta cardinalidad**: en vez de one-hot directo sobre `Product ID`/`Product Name`/`City` (que explotaría dimensionalmente), el pipeline de `LinearRegression` usa **frequency encoding con bucket "other"** para categorías infrecuentes (ver `research.md` Part A) — una técnica simple, determinista, y sin fuga de información del target (a diferencia de un target encoding ingenuo).
- **Split cronológico**: una única función reutilizable (`src/mlops/split.py`) corta por **proporción de filas ordenadas por `Order Date`** (no una fecha calendario fija), compartida por ambos modelos.
- **Artifact registry**: sin infraestructura cloud nueva (no MLflow server, no S3/GCS) — un directorio versionado en disco (`.artifacts/mlops/models/<model_name>/<run_id>/`) más un manifiesto `registry.json` (historial de promociones + puntero activo por ambiente), consistente con el mandato "no new cloud infra" de la constitución y con el patrón de artifacts ya establecido en `001`/`003` (`load_manifest.json`, `semantic_layer.json`).
- **CLI**: tres comandos nuevos en `src/cli/main.py` (`train-sales-model`, `promote-sales-model`, `predict-sales`) siguiendo el patrón de composition-root ya usado por `bootstrap`/`ask`.

## Technical Context

**Language/Version**: Python 3.11+ (estricto — `mypy --strict` ya configurado en `pyproject.toml`; pinned a 3.13 vía `.python-version`). Sin cambios respecto de 001/002/003.

**Primary Dependencies**:
- **`scikit-learn>=1.9`** (NUEVA) — `LinearRegression`, `Pipeline`, `ColumnTransformer`, `OneHotEncoder`, métricas (`mean_squared_error`, `mean_absolute_error`, `r2_score`). Versión estable más reciente verificada en PyPI a la fecha de esta feature.
- **`catboost>=1.2.10`** (NUEVA) — `CatBoostRegressor` con `cat_features` nativo. Versión estable más reciente verificada en PyPI.
- **`joblib`** (dependencia transitiva de `scikit-learn`, ya viene con el paquete) — serialización del `Pipeline` de `LinearRegression`. CatBoost se serializa con su propio formato nativo (`.cbm`, vía `CatBoostRegressor.save_model`/`load_model`) — no requiere `joblib`.
- **`pydantic` v2 (>=2.7, existente)** — todos los contract models nuevos en `src/contracts/mlops.py`.
- **`psycopg` (existente, confinado)** — sin cambios; `src/mlops/` no lo importa (Principle III), solo el adapter `PostgresRepository` ya existente lo usa, inyectado como `QueryProvider` desde el composition root (`cli/main.py`).
- Sin nuevas dependencias de tracking/orquestación (no MLflow client, no Airflow) — el registry es un módulo propio minimalista (ver `research.md` Part C).

**Storage**: PostgreSQL 15 en Docker (sin cambios; solo lectura de `Orders` vía `QueryProvider`, sin escritura). El artifact registry es **filesystem-based**: `.artifacts/mlops/models/<model_name>/<run_id>/` (por-run, inmutable) + `.artifacts/mlops/registry.json` (manifiesto mutable con historial de promociones). No se crean tablas nuevas en Postgres.

**Testing**: `pytest` (existente). Se extienden:
- `tests/contract/test_boundaries.py` — nuevo boundary: `scikit-learn`/`catboost` (y cualquier import de `psycopg`) confinados a `src/mlops/`; ningún módulo bajo `src/mlops/` importa `src/data_engineering/*` ni `src/ai_engineering/*` internals (solo `src/contracts/*` y `src/data_access/interfaces.py`); todos los contract models de `src/contracts/mlops.py` son Pydantic v2 frozen.
- `tests/contract/test_mlops.py` (NUEVO) — contract tests para `SalesFeatureRow`, `ModelRunMetadata`, `EvaluationMetrics`, `ArtifactRegistryEntry`, `PredictionInput`/`PredictionResult` (validación, inmutabilidad, tipos).
- `tests/unit/test_mlops_features.py` (NUEVO) — feature engineering puro (temporal features, `has_discount`, sin DB).
- `tests/unit/test_mlops_split.py` (NUEVO) — split cronológico (cutoff por proporción, sin shuffle, edge cases de test set mínimo).
- `tests/unit/test_mlops_encoding.py` (NUEVO) — frequency/rare-bucket encoder (alta cardinalidad, categoría no vista → "other").
- `tests/unit/test_mlops_registry.py` (NUEVO) — registry (creación de run, promoción, rechazo prod-sin-staging, historial, listado sin deserializar el modelo).
- `tests/integration/test_mlops_training.py` (NUEVO) — `train-sales-model` end-to-end contra Postgres Dockerizado real (ambos modelos, métricas calculadas, dos runs persistidos).
- `tests/integration/test_mlops_reproducibility.py` (NUEVO) — mismo `data_hash` + mismos hiperparámetros ⇒ métricas idénticas (SC-003/FR-014), análogo a `tests/integration/test_reproducibility.py` de `001`.
- `tests/integration/test_mlops_inference.py` (NUEVO) — `predict-sales` sobre un modelo promovido, incluyendo el caso de categoría no vista (FR-024, no debe lanzar excepción).

**Target Platform**: Linux/macOS/Windows local developer machine con Docker. Sin nube, sin servidor web, sin endpoint HTTP (fuera de alcance explícito de la spec).

**Project Type**: Library + CLI tooling (agrega un dominio nuevo, `src/mlops/`, paralelo a `data_engineering`/`ai_engineering`; extiende `src/cli/main.py` con tres comandos).

**Performance Goals**: `train-sales-model` completa en un tiempo razonable para el tamaño actual del dataset (~51k filas de `Orders`) en desarrollo local (segundos a bajos minutos, dominado por el fit de `CatBoostRegressor`); `predict-sales` responde en <2s (SC-006) para una única fila de input, dominado por la carga del artifact desde disco (no hay servicio persistente/warm en v3.0).

**Constraints**:
- `src/mlops/` MUST NOT importar `psycopg`/drivers de base de datos ni SQL crudo directamente (FR-001); MUST NOT importar internals de `data_engineering`/`ai_engineering` (FR-026) — boundary test lo fuerza.
- Split train/test MUST ser cronológico, nunca aleatorio (FR-006).
- Ambos modelos MUST entrenarse sobre el mismo split y evaluarse con las mismas métricas (FR-009, FR-011).
- Todo contract model de entrada/salida MUST ser Pydantic v2 `frozen` (FR-005, FR-027); `mypy --strict` MUST pasar sin errores (FR-028).
- El artifact registry MUST ser todo-o-nada por run (FR-016) e inspeccionable sin deserializar el modelo (FR-015).
- Promoción directa a `prod` sin pasar por `staging` MUST ser rechazada salvo bypass explícito y logueado (FR-018).
- `predict-sales` ante categoría no vista MUST NOT lanzar excepción no controlada (FR-024); MUST señalizar el fallback en la respuesta.

**Scale/Scope**: ~51k filas de `Orders` (una sola tabla fuente); 2 modelos; 3 ambientes de promoción (`dev`/`staging`/`prod`); operador único, sin concurrencia distribuida (per Assumptions del spec, consistente con `003`).

**Open decisions resueltas en Phase 0 investigación** → ver `research.md`:

1. **Estrategia de encoding para categóricas de alta cardinalidad en `LinearRegression`** → Decisión: **frequency encoding + bucket "other" para categorías infrecuentes**, combinado con one-hot para baja/media cardinalidad. Ver research.md Part A.
2. **Estrategia de split cronológico** → Decisión: **corte por proporción de filas ordenadas por `Order Date`** (últimas ~20% como test), configurable y versionado por run. Ver research.md Part B.
3. **Formato del artifact registry** → Decisión: **directorio local versionado por `run_id`** + `registry.json` manifiesto con historial de promociones (mismo patrón de artifact que `001`/`003`, sin backend gestionado). Ver research.md Part C.
4. **Versiones de librerías nuevas** → Decisión: `scikit-learn>=1.9`, `catboost>=1.2.10` (últimas estables verificadas en PyPI). Ver research.md Part D.
5. **Estrategia de hash de datos para reproducibilidad** → Decisión: **SHA-256 sobre una serialización canónica (JSON, `sort_keys=True`) del conjunto de `SalesFeatureRow` extraído**, análogo al `source_sha256` de `001`. Ver research.md Part E.
6. **Manejo de categorías no vistas en inferencia** → Decisión: `OneHotEncoder(handle_unknown="ignore")` + bucket "other" ya absorbe la mayoría de casos nuevos en `LinearRegression`; CatBoost maneja categorías nuevas nativamente. Un flag explícito `used_fallback_encoding` se computa comparando el valor de entrada contra el vocabulario visto en entrenamiento (persistido en el artifact). Ver research.md Part F.
7. **Cómo `src/mlops/` lee `Orders` sin bypass del `QueryProvider`** → Decisión: SQL literal de solo-lectura, validado con el `SqlValidator` existente como defensa en profundidad, ejecutado vía `execute_readonly_query`. Ver research.md Part G.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle (Constitution v1.0.0) | Status | v3.0 plan compliance |
|---|---|---|
| I. Strictly-Typed Python Foundation | PASS | Python 3.11+; `mypy --strict` ya configurado. Todos los contract models nuevos (`SalesFeatureRow`, `ModelRunMetadata`, `EvaluationMetrics`, `ArtifactRegistryEntry`, `PredictionInput`, `PredictionResult`) son Pydantic v2 `frozen` en `src/contracts/mlops.py`, con tipos explícitos en cada función pública y privada de `src/mlops/`. Sin `Any` nuevo salvo donde ya está justificado en contratos existentes (`QueryRow.data: dict[str, Any]`, heredado de `002`); `src/mlops/` NUNCA consume ese `dict` sin tipar más allá del punto de mapeo a `SalesFeatureRow`. |
| II. Layered Separation of Concerns (NON-NEGOTIABLE) | PASS | Nuevo dominio `src/mlops/`, tercero y paralelo a `data_engineering`/`ai_engineering` (constitution: "MLOps: model & AI-artifact lifecycle"). Depende ÚNICAMENTE de `src/contracts/*` y del `QueryProvider` Protocol de `src/data_access/interfaces.py` — NUNCA importa internals de `data_engineering`/`ai_engineering`. Boundary test (`tests/contract/test_boundaries.py`) lo enforced vía AST, igual que en `001`/`003`. La composición del `QueryProvider` concreto (`PostgresRepository`) ocurre en `src/cli/main.py` (composition root), no por import cross-domain interno. |
| III. Portable Data Access & Abstraction | PASS | `src/mlops/` NUNCA importa `psycopg`/drivers específicos; toda lectura pasa por `QueryProvider.execute_readonly_query` (engine-neutral). La futura migración a BigQuery solo requiere que el `BigQueryRepository` implemente el mismo Protocol — `src/mlops/` no cambia. Sin escritura a la base de datos (el registry vive en filesystem, no en Postgres). |
| IV. Data Governance by Default (NON-NEGOTIABLE) | **N/A (justificado) — deferred, no regresión** | `train-sales-model` es un job batch/sistema que entrena sobre el **dataset agregado completo** de `Orders` (no una consulta por-viewer con RLS por `Region`); no hay un "viewer" humano cuya visibilidad deba filtrarse — es análogo a un job de ETL/analytics, no a una consulta de negocio interactiva. Por eso el `GovernedQueryProvider` de `003` (RLS por `Region`) NO se inyecta en el training path: inyectarlo arbitrariamente limitaría el modelo a los datos de un solo viewer, lo cual sería incorrecto para un modelo global de predicción. Esto NO es una regresión de gobernanza (Text-to-SQL/`ask` sigue 100% gobernado sin cambios) — es una superficie nueva (batch ML training) fuera del alcance original de Principle IV, que habla de "access requests resolved against identity/roles" para consultas de negocio. Sí se satisface la porción de auditability de Principle V: `predict-sales` (FR-025) loguea input/output/latencia de cada inferencia (un precursor de observabilidad de producción), y el `data_hash` de cada run deja trazabilidad de qué datos se usaron. Esta decisión y su alcance quedan documentados aquí y en `research.md` Part G como deuda/alcance explícito para una revisión futura de gobernanza sobre el training path (p. ej. si se introdujeran modelos por-región). |
| V. Reproducible MLOps | PASS (esta feature lo satisface por primera vez de forma completa) | Cada run de entrenamiento es versionado (`run_id` único, `params.json`, `metrics.json`, `data_hash.txt`, artifact serializado) y trazable a su commit (vía `git` implícito en el repo) y a su dataset fuente (`data_hash` + `source_sha256` del `load_manifest.json`). Experiment tracking es obligatorio (parte del flujo de `train-sales-model`, no opcional). Promoción por ambientes (`dev`→`staging`→`prod`) con gate explícito (rechaza `prod` sin pasar por `staging`, salvo bypass logueado) — sin despliegue directo a prod. `predict-sales` loguea input/output/latencia (observabilidad básica; drift detection explícitamente fuera de alcance, documentado como deuda en el spec). |

**Gate status**: PASS (con una excepción justificada y no regresiva en Principle IV, documentada arriba — consistente con el patrón de `001`/`002`, donde Principle IV también estuvo marcado como diferido para superficies fuera del alcance de esa feature, hasta que `003` lo resolvió para el camino de consulta de negocio).

## Project Structure

### Documentation (this feature)

```text
specs/004-sales-prediction-model/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (encoding strategy, chronological split,
│                         #   artifact registry format, library versions, data hash,
│                         #   unseen-category fallback, QueryProvider read strategy)
├── data-model.md         # Phase 1 output (MLOps contract models)
├── quickstart.md          # Phase 1 output (runnable end-to-end validation guide)
├── contracts/             # Phase 1 output (typed cross-boundary interfaces)
│   ├── mlops_training.md    # Feature engineering + split + training contract
│   ├── mlops_registry.md    # Artifact registry + promotion contract
│   └── mlops_inference.md   # Prediction/inference contract
└── tasks.md               # Phase 2 output (/speckit.tasks — NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── contracts/                          # Shared typed contracts
│   ├── data_access.py                  # (existing, UNCHANGED) TableDef, ColumnDef,
│   │                                   #   OrderRow, Row...
│   ├── ingestion.py                    # (existing, UNCHANGED)
│   ├── dictionary.py                   # (existing, UNCHANGED)
│   ├── text_to_sql.py                  # (existing, UNCHANGED) QueryRow, QueryResult...
│   ├── semantic_layer.py               # (existing, UNCHANGED)
│   └── mlops.py                        # (NEW) SalesFeatureRow, FeatureSet,
│                                       #       ModelRunMetadata, EvaluationMetrics,
│                                       #       ArtifactRegistryEntry, PromotionRecord,
│                                       #       PredictionInput, PredictionResult
├── data_access/                        # (existing, UNCHANGED) Engine-agnostic data-access
│   ├── interfaces.py                   # (UNCHANGED) — `QueryProvider` Protocol reused
│   │                                   #   as-is; no new methods added to the Protocol
│   └── adapters/postgres/              # (UNCHANGED)
├── data_engineering/                   # (existing, UNCHANGED in this feature)
├── ai_engineering/                     # (existing, UNCHANGED in this feature)
├── mlops/                              # (NEW) MLOps domain — isolated third engineering
│   │                                   #   domain (constitution Principle II)
│   ├── __init__.py
│   ├── features.py                     # Derives `SalesFeatureRow` from `QueryRow`/`OrderRow`
│   │                                   #   (temporal features from Order Date, has_discount).
│   │                                   #   Shared verbatim between training and inference
│   │                                   #   (FR-023, no train/serve skew).
│   ├── dataset.py                      # Reads Orders via `QueryProvider.execute_readonly_query`
│   │                                   #   (validated read-only SQL, no psycopg), builds the
│   │                                   #   `FeatureSet`, computes the deterministic `data_hash`.
│   ├── split.py                        # Chronological train/test split (cutoff by proportion
│   │                                   #   of rows ordered by Order Date), shared by both models.
│   ├── encoding.py                     # `FrequencyRareBucketEncoder` — sklearn-compatible
│   │                                   #   transformer for high-cardinality columns
│   │                                   #   (Product ID/Product Name/City): frequency encoding
│   │                                   #   + "other" bucket for infrequent categories.
│   ├── linear_model.py                 # Builds & fits the `LinearRegression` `Pipeline`/
│   │                                   #   `ColumnTransformer` (one-hot for low/mid cardinality
│   │                                   #   + `FrequencyRareBucketEncoder` for high cardinality).
│   ├── catboost_model.py               # Builds & fits `CatBoostRegressor` with native
│   │                                   #   `cat_features`.
│   ├── evaluation.py                   # Computes RMSE/MAE/R² on the shared test set for a
│   │                                   #   fitted model; produces `EvaluationMetrics`.
│   ├── training.py                     # Orchestrates: dataset -> split -> train both models ->
│   │                                   #   evaluate -> persist both runs. Consumed by CLI
│   │                                   #   `train-sales-model`.
│   ├── registry.py                     # File-based artifact registry: run persistence
│   │                                   #   (`params.json`/`metrics.json`/`data_hash.txt`/model
│   │                                   #   artifact), `registry.json` manifest read/write,
│   │                                   #   promotion (dev/staging/prod gate + history), listing
│   │                                   #   without deserializing the model.
│   └── inference.py                    # Loads the promoted model for an environment and
│                                       #   predicts `Sales` from a `PredictionInput`, reusing
│                                       #   `features.py`; computes `used_fallback_encoding`.
└── cli/
    └── main.py                          # (MODIFIED) adds `train-sales-model`,
                                        #   `promote-sales-model`, `predict-sales` commands;
                                        #   composes `PostgresRepository` as the `QueryProvider`
                                        #   injected into `src/mlops/` (composition root,
                                        #   consistent with `bootstrap`/`ask`).

# New artifact directory entries (filesystem-based registry — no new cloud infra)
.artifacts/
├── load_manifest.json                  # (existing, UNCHANGED)
├── text_to_sql.log                     # (existing, UNCHANGED)
├── semantic_layer.json                 # (existing, UNCHANGED)
├── semantic_layer.md                   # (existing, UNCHANGED)
├── mlops/
│   ├── registry.json                   # (NEW) manifest: per-environment active run_id +
│   │                                   #   full promotion history (per FR-019)
│   ├── predict_sales.log               # (NEW) governance-aware inference log (FR-025)
│   └── models/
│       ├── linear_regression/
│       │   └── <run_id>/
│       │       ├── params.json         # hyperparameters + library versions
│       │       ├── metrics.json        # RMSE/MAE/R², train/test size, split cutoff date
│       │       ├── data_hash.txt       # SHA-256 of the extracted training FeatureSet
│       │       └── model.joblib        # serialized sklearn Pipeline
│       └── catboost/
│           └── <run_id>/
│               ├── params.json
│               ├── metrics.json
│               ├── data_hash.txt
│               └── model.cbm           # serialized CatBoost native format

pyproject.toml                          # (MODIFIED) adds `scikit-learn>=1.9`,
                                        #   `catboost>=1.2.10` to `[project.dependencies]`

tests/
├── contract/
│   ├── test_boundaries.py              # (MODIFIED) extends existing asserts: (a)
│   │                                   #   `scikit-learn`/`catboost` confined to `src/mlops/`;
│   │                                   #   (b) `src/mlops/` never imports `psycopg` or
│   │                                   #   `data_engineering`/`ai_engineering` internals;
│   │                                   #   (c) MLOps contracts are Pydantic v2 frozen.
│   └── test_mlops.py                   # (NEW) contract tests for all `src/contracts/mlops.py`
│                                       #   models (validation, immutability, typed fields).
├── unit/
│   ├── test_mlops_features.py          # (NEW) pure feature engineering (no DB).
│   ├── test_mlops_split.py             # (NEW) chronological split (no shuffle, min test size).
│   ├── test_mlops_encoding.py          # (NEW) frequency/rare-bucket encoder.
│   └── test_mlops_registry.py          # (NEW) registry: run persistence, promotion gate,
│                                       #   history, listing without deserializing the model.
└── integration/
    ├── test_mlops_training.py          # (NEW) `train-sales-model` end-to-end against
    │                                   #   Dockerized PostgreSQL.
    ├── test_mlops_reproducibility.py   # (NEW) same data_hash + same hyperparams => identical
    │                                   #   metrics (SC-003).
    └── test_mlops_inference.py         # (NEW) `predict-sales` on a promoted model, incl.
                                        #   unseen-category fallback (FR-024).
```

**Structure Decision**: Single-project layout (extendiendo Option 1 de `001`/`002`/`003`). Se agrega un **tercer dominio de ingeniería paralelo**, `src/mlops/`, que mirror-ea el estilo de organización interno ya usado por `src/data_engineering/semantic_layer/` (un subpaquete con módulos de responsabilidad única — builder/resolver/registry/metrics/render allá; features/dataset/split/encoding/linear_model/catboost_model/evaluation/training/registry/inference acá). Sus contract models viven en `src/contracts/mlops.py` (typed boundary — Principle I/II), sin modificar ningún contrato existente. `src/data_access/interfaces.py` NO se modifica — `src/mlops/` reutiliza el `QueryProvider` Protocol tal cual existe hoy (mismo patrón que `ai_engineering`). La inyección del `PostgresRepository` concreto como `QueryProvider` ocurre en `src/cli/main.py` (composition root), nunca por import cross-domain interno. `scikit-learn`/`catboost` quedan confinados a `src/mlops/` (boundary test), análogo a como `psycopg` está confinado al adapter y `openai` a `ai_engineering`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations que requieran una alternativa más simple rechazada. La única entrada no-trivial de la Constitution Check es Principle IV, marcada **N/A (justificado)** — no es una violación sino un alcance explícitamente fuera del propósito de esa regla (ver tabla arriba); no se incluye aquí porque no hay alternativa "más simple" que evaluar (no hay complejidad añadida a justificar, sino una decisión de alcance). Table intentionally left mostly empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|---------------------------------------|
| N/A | — | — |
