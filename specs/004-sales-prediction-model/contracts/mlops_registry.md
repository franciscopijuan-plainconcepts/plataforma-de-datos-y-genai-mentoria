# Contract: MLOps Artifact Registry & Staged Promotion

**Feature**: 004-sales-prediction-model
**Date**: 2026-08-25
**Related**: [research.md](../research.md) Part C · [data-model.md](../data-model.md) · [mlops_training.md](./mlops_training.md) · [mlops_inference.md](./mlops_inference.md)

> Define la interfaz típada del artifact registry basado en archivos (`.artifacts/mlops/`) y de la promoción por ambiente. Vive en `src/mlops/registry.py`. Consumido por `train-sales-model` (persistencia), `promote-sales-model` (promoción) y `predict-sales` (resolución del `run_id` activo). Ver constitution Principle V (Reproducible MLOps, NON-NEGOTIABLE-adjacent).

## Filesystem layout (contract, not just an artifact convention)

```text
.artifacts/mlops/
├── registry.json                      # ArtifactRegistryDocument (data-model.md § "registry.json")
└── models/
    ├── linear_regression/<run_id>/{params.json, metrics.json, data_hash.txt, model.joblib}
    └── catboost/<run_id>/{params.json, metrics.json, data_hash.txt, model.cbm}
```

Every `<run_id>` directory is written via a **temp-then-rename** pattern
(write to `<run_id>.tmp/`, then `os.rename` to `<run_id>/` only after all
four files are written successfully) so a crash mid-write NEVER leaves a
partial/corrupt run visible to `list_runs()` (FR-016).

## `ArtifactRegistry` interface (`src/mlops/registry.py`)

```python
class ArtifactRegistry:
    """File-based experiment tracking / artifact registry. Owns
    `.artifacts/mlops/` — no other module reads/writes that directory
    directly (single point of truth for the registry's on-disk format).
    """

    def __init__(self, root: Path = Path(".artifacts/mlops")) -> None: ...

    def persist_run(
        self,
        model_name: Literal["linear_regression", "catboost"],
        fitted_model: Union[Pipeline, CatBoostRegressor],
        run_metadata: ModelRunMetadata,   # run_id not yet assigned by the caller
        metrics: EvaluationMetrics,
    ) -> ArtifactRegistryEntry:
        """Generates a fresh unique `run_id`, serializes `fitted_model` to the
        correct format for `model_name` (`model.joblib` via `joblib.dump` for
        `linear_regression`; `model.cbm` via `CatBoostRegressor.save_model` for
        `catboost`), writes `params.json`/`metrics.json`/`data_hash.txt`
        atomically (temp-then-rename, FR-016), appends a summary entry to
        `registry.json` (also written atomically), and returns the resulting
        `ArtifactRegistryEntry`.

        MUST NOT partially update `registry.json` if any per-run file write
        fails — the whole operation is all-or-nothing (FR-016).
        """

    def list_runs(self, model_name: Literal["linear_regression", "catboost"] | None = None) -> list[ArtifactRegistryEntry]:
        """Returns all known runs (optionally filtered by `model_name`) by
        reading ONLY `registry.json` — never deserializes `model.joblib`/
        `model.cbm` (FR-015). Ordered by `trained_at` descending (most
        recent first).
        """

    def promote(
        self,
        run_id: str,
        environment: Literal["dev", "staging", "prod"],
        force_bypass_staging_gate: bool = False,
    ) -> PromotionRecord:
        """Validates `run_id` exists in `registry.json` — raises a typed
        error listing available `run_id`s if not (FR-020). Enforces the
        staging gate (FR-018): promoting to `prod` when `run_id` was NEVER
        previously promoted to `staging` (checked against
        `promotion_history`) is REJECTED unless
        `force_bypass_staging_gate=True`, in which case the promotion
        proceeds AND `PromotionRecord.bypassed_staging_gate=True` is set
        (a governance event, logged by the CLI per FR-018).

        Appends (never overwrites) a `PromotionRecord` to
        `registry.json::promotion_history` (FR-019 — history preserved) and
        atomically rewrites the manifest.
        """

    def resolve_active_run(
        self, environment: Literal["dev", "staging", "prod"]
    ) -> ArtifactRegistryEntry | None:
        """Returns the currently active `ArtifactRegistryEntry` for
        `environment` (the most recent `PromotionRecord` for that
        environment in `promotion_history`), or `None` if nothing was ever
        promoted there. Used by `predict-sales` (FR-022: fail fast with a
        clear message when `None`).
        """

    def load_model(self, entry: ArtifactRegistryEntry) -> Union[Pipeline, CatBoostRegressor]:
        """Deserializes the actual model artifact (`model.joblib` or
        `model.cbm`, dispatched by `entry.model_name`) for `entry.run_id`.
        Called ONLY by `src/mlops/inference.py` at prediction time — never
        by `list_runs`/`promote`/`resolve_active_run` (those stay
        artifact-agnostic, satisfying FR-015).
        """
```

## Promotion gate semantics (FR-017..FR-020)

| Scenario | Behavior |
|---|---|
| Promote existing `run_id` to `dev` or `staging` | Always allowed — no gate. |
| Promote `run_id` to `prod`, and that `run_id` was previously promoted to `staging` at least once (per `promotion_history`) | Allowed, `bypassed_staging_gate=False`. |
| Promote `run_id` to `prod`, never promoted to `staging`, `force_bypass_staging_gate=False` (default) | REJECTED — raises a typed error explaining the requirement (US3 AC2). |
| Promote `run_id` to `prod`, never promoted to `staging`, `force_bypass_staging_gate=True` | Allowed, `bypassed_staging_gate=True` — CLI (`promote-sales-model --force`) MUST log this as an explicit governance event (FR-018, `_info`/structured log line, analogous to the `gov_bypass` field already logged by `ai_engineering/pipeline.py`). |
| Promote a `run_id` that does not exist in `registry.json` | REJECTED — raises a typed error listing all known `run_id`s (FR-020). |
| Promote a new `run_id` to an environment that already has an active model | Allowed — the previous `PromotionRecord` is NOT deleted/overwritten; it remains in `promotion_history`, only superseded as "current" by recency (US3 AC4). |

## Domain boundary rules (enforced by `tests/contract/test_boundaries.py` / `tests/unit/test_mlops_registry.py`)

- `ArtifactRegistry` is the ONLY component that reads/writes `.artifacts/mlops/**` — `training.py`/`inference.py`/the CLI never touch the filesystem paths directly (they go through `ArtifactRegistry` methods), so the on-disk format can evolve without touching callers.
- `registry.json` MUST always be valid JSON parseable into `ArtifactRegistryDocument` after any `persist_run`/`promote` call — a unit test asserts the file round-trips through `ArtifactRegistryDocument.model_validate_json(...)` after every mutating operation, including simulated failures (a failing write MUST leave the PREVIOUS valid `registry.json` untouched, never a half-written one).
- No `psycopg`/database dependency anywhere in `registry.py` (Principle III) — it is pure filesystem + JSON.
