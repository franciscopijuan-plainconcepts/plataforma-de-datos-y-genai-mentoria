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
