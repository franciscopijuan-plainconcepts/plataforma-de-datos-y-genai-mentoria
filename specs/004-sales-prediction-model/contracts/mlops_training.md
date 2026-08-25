# Contract: MLOps Training (Feature Engineering, Split, Fit, Evaluate)

**Feature**: 004-sales-prediction-model
**Date**: 2026-08-25
**Related**: [research.md](../research.md) · [data-model.md](../data-model.md) · [mlops_registry.md](./mlops_registry.md)

> Define la interfaz típada del flujo `train-sales-model`: cómo `src/mlops/` lee `Orders` vía `QueryProvider`, deriva `SalesFeatureRow`, particiona cronológicamente, entrena ambos modelos, y produce `EvaluationMetrics`. Consumido por `src/cli/main.py::cmd_train_sales_model`. Ver constitution Principles I, II, III, V.

## Módulos y firmas

### `src/mlops/dataset.py`

```python
def extract_feature_set(query_provider: QueryProvider, orders_table_def: TableDef) -> FeatureSet:
    """Reads all `Orders` rows via `QueryProvider.execute_readonly_query` (a
    literal, trusted, read-only SQL validated by `SqlValidator` — research.md
    Part G), maps each `QueryRow` to a `SalesFeatureRow` (via
    `src/mlops/features.py`), sorts deterministically by `order_date`, and
    computes the canonical `data_hash` (research.md Part E).

    MUST NOT import `psycopg` or any engine-specific driver (FR-001).
    Raises a clear, typed error (not a bare `Exception`) if PostgreSQL is
    unreachable — `train-sales-model` MUST fail fast without producing a
    partial run (Edge Cases: "Postgres no disponible").
    """
```

### `src/mlops/features.py`

```python
def derive_training_row(order: OrderRow) -> SalesFeatureRow:
    """Derives a `SalesFeatureRow` (WITH `sales` populated) from an `OrderRow`.
    Used by `dataset.py` during training extraction.
    """

def derive_prediction_row(input_row: PredictionInput) -> SalesFeatureRow:
    """Derives a `SalesFeatureRow` (WITHOUT `sales`, i.e. `sales=None`) from a
    `PredictionInput`. MUST call the exact same internal temporal-feature and
    `has_discount` derivation logic as `derive_training_row` (FR-023 — no
    parallel reimplementation, no train/serve skew). Both public functions
    delegate to a shared private helper, e.g. `_derive_temporal_features(order_date)`
    and `_derive_has_discount(discount)`.
    """
```

**Contract invariant**: `_derive_temporal_features` and `_derive_has_discount` are
the SINGLE source of truth for feature derivation, called by both
`derive_training_row` and `derive_prediction_row`. A contract test
(`tests/contract/test_mlops.py`) asserts both public functions route through
the same private helpers (via introspection or by asserting identical output
for equivalent inputs), guarding against train/serve skew regressions.

### `src/mlops/split.py`

```python
def chronological_split(
    rows: list[SalesFeatureRow],
    test_fraction: float = 0.2,
    min_test_rows: int = 500,
) -> tuple[list[SalesFeatureRow], list[SalesFeatureRow]]:
    """Sorts `rows` by `order_date` ascending (idempotent if already sorted)
    and splits by ROW-PROPORTION cutoff (research.md Part B) — NEVER shuffles.
    Returns `(train_rows, test_rows)`.

    Raises `ValueError` with a clear, actionable message if the resulting
    test set would have fewer than `min_test_rows` rows (Edge Cases: "test
    set cronológico demasiado pequeño") — the caller (`training.py`) MUST
    surface this as a fail-fast CLI error, not a partial/degenerate run.
    """
```

**Contract invariant**: called EXACTLY ONCE per `train-sales-model` invocation,
with the SAME `test_fraction`/`min_test_rows`, and its `(train_rows, test_rows)`
output is passed UNCHANGED to both `linear_model.py` and `catboost_model.py`
(FR-009 — same split for both models is what makes the metrics comparison valid).

### `src/mlops/linear_model.py`

```python
def build_pipeline(hyperparameters: dict[str, Union[str, int, float, bool]]) -> Pipeline:
    """Builds an unfit sklearn `Pipeline` wrapping a `ColumnTransformer`:
    - `OneHotEncoder(handle_unknown="ignore")` for low/mid-cardinality columns
      (`ship_mode`, `segment`, `region`, `market`, `sub_category`,
      `category`, plus the temporal categorical-like fields).
    - `FrequencyRareBucketEncoder` (from `src/mlops/encoding.py`) for the
      high-cardinality column (`product_id`). NOTE (Amendment 2026-08-25):
      `product_name`/`city`/`state`/`country` were dropped from the feature
      set — see `spec.md` § Amendment.
    - `LinearRegression(**hyperparameters)` as the final estimator.
    """

def fit_linear_model(
    train_rows: list[SalesFeatureRow], hyperparameters: dict[str, Union[str, int, float, bool]]
) -> tuple[Pipeline, ModelRunMetadata]:
    """Builds (via `build_pipeline`) and fits the pipeline on `train_rows`.
    Returns the fitted `Pipeline` plus a `ModelRunMetadata` (model_name=
    "linear_regression") WITHOUT `run_id`/`artifact_path` populated yet (those
    are assigned by `registry.py` at persistence time) — `training.py` fills
    them in after a successful `registry.persist_run(...)` call.
    """
```

### `src/mlops/catboost_model.py`

```python
def fit_catboost_model(
    train_rows: list[SalesFeatureRow], hyperparameters: dict[str, Union[str, int, float, bool]]
) -> tuple[CatBoostRegressor, ModelRunMetadata]:
    """Converts `train_rows` to CatBoost's expected input (feature matrix +
    target array + `cat_features` index list for `ship_mode`, `segment`,
    `region`, `market`, `product_id`, `sub_category`, `category` — ALL
    categorical columns passed natively, per FR-008, no manual encoding).
    Fits a `CatBoostRegressor(**hyperparameters)`. Same `ModelRunMetadata`
    contract as `fit_linear_model` (model_name="catboost").
    """
```

### `src/mlops/evaluation.py`

```python
def evaluate(
    model: Union[Pipeline, CatBoostRegressor],
    test_rows: list[SalesFeatureRow],
    split_cutoff_date: datetime,
) -> EvaluationMetrics:
    """Predicts on `test_rows` (features only, `sales` excluded from the
    input matrix) and computes RMSE, MAE, R² against the true `sales` values
    (FR-011). Works identically for BOTH model types — the function is
    polymorphic over any object exposing `.predict(X) -> array-like`, which
    both `Pipeline` and `CatBoostRegressor` satisfy.
    """
```

### `src/mlops/training.py`

```python
def train_sales_models(
    query_provider: QueryProvider,
    orders_table_def: TableDef,
    registry: ArtifactRegistry,  # from src/mlops/registry.py
    linear_hyperparameters: dict[str, Union[str, int, float, bool]] | None = None,
    catboost_hyperparameters: dict[str, Union[str, int, float, bool]] | None = None,
    test_fraction: float = 0.2,
    min_test_rows: int = 500,
) -> tuple[ArtifactRegistryEntry, ArtifactRegistryEntry]:
    """Orchestrates the full US1 flow (FR-010): extract -> split -> fit both
    models -> evaluate both -> persist both runs (FR-013, todo-or-nothing
    per run, FR-016). Returns the two persisted `ArtifactRegistryEntry`
    (linear_regression first, catboost second) so the CLI can print the
    side-by-side comparison (US1 AC2) and identify the better RMSE (FR-012).

    Uses documented, versioned default hyperparameters when the `*_hyperparameters`
    arguments are omitted (Assumptions: "hiperparámetros por defecto razonables") —
    the resolved defaults are always recorded in `ModelRunMetadata.hyperparameters`
    (never silently applied without being persisted, so reproducibility — FR-014 —
    holds even for default-hyperparameter runs).
    """
```

## Domain boundary rules (enforced by `tests/contract/test_boundaries.py`)

- `src/mlops/*.py` MUST NOT `import psycopg` (or any submodule thereof), directly or transitively via a re-export — only `src/data_access/adapters/postgres/` may do so (existing boundary, extended to also assert `src/mlops` is NOT in the allowed-psycopg set).
- `src/mlops/*.py` MUST NOT `import src.data_engineering.*` or `import src.ai_engineering.*` (no cross-domain internal imports — Principle II).
- `sklearn`/`catboost` imports MUST be confined to `src/mlops/*.py` (new boundary, analogous to the existing `pandas`/`openpyxl` → `data_engineering/eda|ingestion` and `openai` → `ai_engineering` confinement rules).
- Every public function above accepts/returns ONLY: contract models from `src/contracts/{data_access,text_to_sql,mlops}.py`, the `QueryProvider`/`TableDef` Protocol types, or well-known third-party model objects (`Pipeline`, `CatBoostRegressor`) that are themselves confined to `src/mlops/` (never returned across the CLI boundary — the CLI only ever receives `ArtifactRegistryEntry`/`EvaluationMetrics`, never a raw fitted model object).
