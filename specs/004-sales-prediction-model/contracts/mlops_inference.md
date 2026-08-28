# Contract: MLOps Inference (`predict-sales`)

**Feature**: 004-sales-prediction-model
**Date**: 2026-08-25
**Related**: [research.md](../research.md) Part F · [data-model.md](../data-model.md) · [mlops_training.md](./mlops_training.md) · [mlops_registry.md](./mlops_registry.md)

> Define la interfaz típada de inferencia batch/CLI sobre un modelo promovido a un ambiente. Vive en `src/mlops/inference.py`. Consumido por `src/cli/main.py::cmd_predict_sales`. Ver constitution Principle I (typed I/O), Principle V (observability groundwork).

## `src/mlops/inference.py`

```python
def predict_sales(
    registry: ArtifactRegistry,
    environment: Literal["dev", "staging", "prod"],
    prediction_input: PredictionInput,
    predictions_repository: PredictionsRepository | None = None,
) -> PredictionResult:
    """End-to-end inference (FR-021):

    1. `entry = registry.resolve_active_run(environment)` — if `None`, raise
       a typed, clear-message error (FR-022: "no hay modelo activo en
       <environment>"). The CLI catches this and fails fast (`_err(...)`),
       consistent with `bootstrap`/`ask` fail-fast conventions.
    2. `model = registry.load_model(entry)`.
    3. `feature_row = derive_prediction_row(prediction_input)` — from
       `src/mlops/features.py`, the SAME function used at training time
       (FR-023, no train/serve skew).
    4. Build the model's expected input matrix from `feature_row` (same
       column layout used for the model's `model_name` — `linear_model.py`'s
       `ColumnTransformer` for `linear_regression`, or the raw categorical
       feature vector + `cat_features` for `catboost`).
    5. `used_fallback_encoding = _check_unseen_categories(feature_row, entry)`
       — compares each categorical value in `feature_row` against the
       vocabulary learned at training time (persisted inside the loaded
       model: `OneHotEncoder.categories_` / `FrequencyRareBucketEncoder`'s
       learned frequency table / CatBoost's known category set). `True` if
       ANY categorical field was not seen during training (research.md
       Part F). MUST NOT raise — sklearn's `handle_unknown="ignore"` and
       CatBoost's native handling already guarantee `.predict()` does not
       throw on unseen categories (FR-024).
    6. `predicted_sales = Decimal(str(round(float(model.predict([...])[0]), 2)))`.
    7. Returns a `PredictionResult` with `run_id=entry.run_id`,
       `model_name=entry.model_name`, `environment=environment`,
       `used_fallback_encoding`, and `latency_ms` measured from step 1 to
       step 6 (SC-006: MUST be well under 2s in local dev).
    8. Logs the call (FR-025, `_log_prediction_call(...)`, analogous to
       `ai_engineering/pipeline.py::_log_call`): timestamp,
       `prediction_input` (subject to governance — see note below),
       `predicted_sales`, `run_id`, `model_name`, `environment`,
       `used_fallback_encoding`, `latency_ms`, appended to
       `.artifacts/mlops/predict_sales.log`.
    9. **(Amendment 2026-08-26)** If `predictions_repository` is provided
       (a `PredictionsRepository` — structurally, anything satisfying both
       `SchemaProvider` and `DataProvider`, e.g. `PostgresRepository`), also
       persists the same information as one row in the `Predictions` SQL
       table via `src/mlops/predictions_store.py::persist_prediction`
       (data-model.md § 9). This param is `None`-default and OPTIONAL —
       `predict_sales` MUST still work with no DB connection at all (see
       "Domain boundary rules" below); the CLI (`cmd_predict_sales`) passes
       a live `PostgresRepository` best-effort (falls back to `None`, i.e.
       JSONL-only logging, if Postgres is unreachable).
    """
```

### Governance note on FR-025 logging

Per the Constitution Check in `plan.md` (Principle IV entry), `predict-sales`
does not run behind a `SemanticViewer`/RLS gate (there is no per-viewer data
access happening — the model artifact itself, not a live query, produces the
prediction). The logging in step 8 is a **precursor to production
observability** (Principle V: "input/output ... latency ... logged for every
call"), not a governance/RLS enforcement point — it satisfies the *auditability*
half of Principle V while explicitly NOT claiming RLS/RBAC coverage for this
CLI-only, non-viewer-scoped surface. This scope is intentional and documented,
not an oversight.

## Unseen-category detection contract (`_check_unseen_categories`)

```python
def _check_unseen_categories(
    feature_row: SalesFeatureRow, entry: ArtifactRegistryEntry
) -> bool:
    """Pure function (no I/O beyond the already-loaded model object). Checks
    each of the 7 categorical fields of `feature_row`
    (`ship_mode, segment, region, market, product_id, sub_category, category`)
    against the vocabulary the loaded model learned in training (Amendment
    2026-08-25: `city`/`state`/`country`/`product_name` dropped — see
    `spec.md` § Amendment). Returns `True` on the FIRST unseen value found
    (short-circuits — no need to enumerate all mismatches for this feature).
    """
```

**Contract invariant**: this function is deterministic and side-effect-free —
given the same `feature_row` and the same loaded model artifact, it always
returns the same boolean. Verified by `tests/unit/test_mlops_registry.py` (or
a dedicated `tests/unit/test_mlops_inference.py` if introduced in Phase 2)
with a fixture model trained on a small known vocabulary.

## Domain boundary rules (enforced by `tests/contract/test_boundaries.py`)

- `src/mlops/inference.py` MUST NOT construct SQL or touch `QueryProvider` —
  inference operates ENTIRELY on the already-loaded model artifact + the
  typed `PredictionInput`; it has NO **mandatory** dependency on Postgres
  being reachable (US4, Edge Cases: inference does not require a live DB
  connection to produce a prediction). **Amendment (2026-08-26)**: it now
  accepts an OPTIONAL `predictions_repository` for best-effort historic
  persistence into the `Predictions` SQL table (step 9 above) — this is
  additive and never blocks or fails the prediction itself; when omitted
  (`None`), behavior is identical to before the amendment.
- `src/mlops/predictions_store.py` (new, Amendment 2026-08-26) builds the
  `Predictions` `TableDef`/`PredictionRow` and calls
  `SchemaProvider.create_table`/`DataProvider.load_rows` — same engine-neutral
  Protocols already used by `data_engineering` for `Orders`/`Returns`/`People`
  (`src/data_access/interfaces.py`). It does not import `psycopg` or any
  adapter internals, consistent with FR-026.
- `src/mlops/inference.py` MUST import `derive_prediction_row` from
  `src/mlops/features.py` (not reimplement feature derivation) — a contract
  test asserts no duplicate temporal-feature/`has_discount` logic exists
  outside `features.py` (grep/AST check, analogous to the `pandas`/`psycopg`
  confinement checks in `tests/contract/test_boundaries.py`).
- `PredictionInput`/`PredictionResult` are the ONLY types crossing the
  `cli/main.py` ⇄ `src/mlops/inference.py` boundary — the CLI never receives
  a raw `Pipeline`/`CatBoostRegressor` object or an untyped `dict` (Principle I).
