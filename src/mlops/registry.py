"""Filesystem-backed artifact registry for MLOps runs and promotions."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from uuid import uuid4

import joblib
from catboost import CatBoostRegressor
from sklearn.pipeline import Pipeline

from src.contracts.mlops import (
    ArtifactRegistryDocument,
    ArtifactRegistryEntry,
    EnvironmentName,
    EvaluationMetrics,
    ModelName,
    ModelRunMetadata,
    PromotionRecord,
)


class RegistryError(RuntimeError):
    """Base error for registry operations."""


class UnknownRunIdError(RegistryError):
    """Raised when a requested run_id does not exist."""


class PromotionGateError(RegistryError):
    """Raised when a promotion violates staging/prod rules."""


class NoActiveModelError(RegistryError):
    """Raised when an environment has no promoted model."""


class ArtifactRegistry:
    """Owns the on-disk MLOps registry layout."""

    def __init__(self, root: Path = Path(".artifacts/mlops")) -> None:
        self._root = root
        self._models_root = self._root / "models"
        self._registry_path = self._root / "registry.json"
        self._root.mkdir(parents=True, exist_ok=True)
        self._models_root.mkdir(parents=True, exist_ok=True)

    def persist_run(
        self,
        model_name: ModelName,
        fitted_model: Pipeline | CatBoostRegressor,
        run_metadata: ModelRunMetadata,
        metrics: EvaluationMetrics,
    ) -> ArtifactRegistryEntry:
        """Persist a fully trained run atomically and return its registry entry."""
        document = self._load_document()
        run_id = self._generate_unique_run_id(document)
        artifact_filename = "model.joblib" if model_name == "linear_regression" else "model.cbm"
        temp_run_dir = self._models_root / model_name / f"{run_id}.tmp"
        final_run_dir = self._models_root / model_name / run_id
        temp_run_dir.parent.mkdir(parents=True, exist_ok=True)
        if temp_run_dir.exists():
            shutil.rmtree(temp_run_dir)
        temp_run_dir.mkdir(parents=True, exist_ok=False)

        updated_metadata = run_metadata.model_copy(
            update={
                "run_id": run_id,
                "artifact_path": str((Path("models") / model_name / run_id / artifact_filename).as_posix()),
            }
        )
        entry = ArtifactRegistryEntry(
            run_id=run_id,
            model_name=model_name,
            trained_at=updated_metadata.trained_at,
            metrics=metrics,
            promoted_environments=[],
        )
        updated_document = document.model_copy(update={"runs": [*document.runs, entry]})
        updated_document = self._with_derived_promotions(updated_document)

        try:
            self._serialize_model(fitted_model, model_name, temp_run_dir / artifact_filename)
            self._write_json(
                temp_run_dir / "params.json",
                {
                    **updated_metadata.model_dump(mode="json"),
                    "categorical_vocabularies": getattr(
                        fitted_model, "_mlops_categorical_vocabularies", {}
                    ),
                },
            )
            self._write_json(
                temp_run_dir / "metrics.json", metrics.model_dump(mode="json")
            )
            (temp_run_dir / "data_hash.txt").write_text(
                updated_metadata.data_hash,
                encoding="utf-8",
            )
            temp_run_dir.replace(final_run_dir)
            self._write_document(updated_document)
        except Exception as exc:
            if temp_run_dir.exists():
                shutil.rmtree(temp_run_dir, ignore_errors=True)
            if final_run_dir.exists() and not self._run_is_listed(run_id):
                shutil.rmtree(final_run_dir, ignore_errors=True)
            raise RegistryError(f"Could not persist model run {run_id}: {exc}") from exc

        return self._with_derived_promotions(updated_document).runs[-1]

    def list_runs(self, model_name: ModelName | None = None) -> list[ArtifactRegistryEntry]:
        """List known runs from the registry manifest only."""
        document = self._with_derived_promotions(self._load_document())
        runs = document.runs
        if model_name is not None:
            runs = [entry for entry in runs if entry.model_name == model_name]
        return sorted(runs, key=lambda entry: entry.trained_at, reverse=True)

    def promote(
        self,
        run_id: str,
        environment: EnvironmentName,
        force_bypass_staging_gate: bool = False,
    ) -> PromotionRecord:
        """Promote an existing run into an environment with stage-gate checks."""
        document = self._load_document()
        known_run_ids = {entry.run_id for entry in document.runs}
        if run_id not in known_run_ids:
            available = sorted(known_run_ids)
            raise UnknownRunIdError(
                f"Unknown run_id {run_id!r}. Available run_ids: {available}"
            )

        has_staging_promotion = any(
            record.environment == "staging" and record.run_id == run_id
            for record in document.promotion_history
        )
        bypassed = False
        if environment == "prod" and not has_staging_promotion:
            if not force_bypass_staging_gate:
                raise PromotionGateError(
                    f"Run {run_id!r} must be promoted to staging before prod. "
                    "Use --force to bypass explicitly."
                )
            bypassed = True

        record = PromotionRecord(
            environment=environment,
            run_id=run_id,
            promoted_at=datetime.now(timezone.utc),
            bypassed_staging_gate=bypassed,
        )
        updated_document = document.model_copy(
            update={
                "promotion_history": [*document.promotion_history, record],
            }
        )
        self._write_document(self._with_derived_promotions(updated_document))
        return record

    def resolve_active_run(
        self, environment: EnvironmentName
    ) -> ArtifactRegistryEntry | None:
        """Return the active run for an environment, or None when absent."""
        document = self._with_derived_promotions(self._load_document())
        latest_record: PromotionRecord | None = None
        for record in document.promotion_history:
            if record.environment == environment:
                latest_record = record
        if latest_record is None:
            return None
        for entry in document.runs:
            if entry.run_id == latest_record.run_id:
                return entry
        return None

    def read_run_metadata(self, entry: ArtifactRegistryEntry) -> ModelRunMetadata:
        """Load persisted run metadata for reporting purposes."""
        params = self._load_run_params(entry)
        params.pop("categorical_vocabularies", None)
        return ModelRunMetadata.model_validate(params)

    def load_model(self, entry: ArtifactRegistryEntry) -> Pipeline | CatBoostRegressor:
        """Load the model artifact and attach persisted categorical vocabularies."""
        params = self._load_run_params(entry)
        run_dir = self._models_root / entry.model_name / entry.run_id
        categorical_vocabularies = params.get("categorical_vocabularies", {})
        if entry.model_name == "linear_regression":
            model = cast(Pipeline, joblib.load(run_dir / "model.joblib"))
        else:
            model = CatBoostRegressor()
            model.load_model(str(run_dir / "model.cbm"))
        setattr(model, "_mlops_categorical_vocabularies", categorical_vocabularies)
        return model

    def _load_run_params(self, entry: ArtifactRegistryEntry) -> dict[str, object]:
        run_dir = self._models_root / entry.model_name / entry.run_id
        return cast(
            dict[str, object],
            json.loads((run_dir / "params.json").read_text(encoding="utf-8")),
        )

    def _load_document(self) -> ArtifactRegistryDocument:
        if not self._registry_path.exists():
            return ArtifactRegistryDocument(version="1.0.0")
        payload = self._registry_path.read_text(encoding="utf-8")
        try:
            return ArtifactRegistryDocument.model_validate_json(payload)
        except Exception as exc:
            raise RegistryError(f"registry.json is invalid: {exc}") from exc

    def _write_document(self, document: ArtifactRegistryDocument) -> None:
        validated_document = ArtifactRegistryDocument.model_validate_json(
            document.model_dump_json()
        )
        temp_path = self._registry_path.with_suffix(".json.tmp")
        temp_path.write_text(
            validated_document.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._registry_path)

    def _generate_unique_run_id(self, document: ArtifactRegistryDocument) -> str:
        known = {entry.run_id for entry in document.runs}
        for _ in range(10):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            run_id = f"{timestamp}-{uuid4().hex[:8]}"
            if run_id not in known:
                return run_id
        raise RegistryError("Could not generate a unique run_id after 10 attempts")

    def _serialize_model(
        self,
        fitted_model: Pipeline | CatBoostRegressor,
        model_name: ModelName,
        destination: Path,
    ) -> None:
        if model_name == "linear_regression":
            joblib.dump(fitted_model, destination)
        else:
            fitted_model.save_model(str(destination))

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _active_promotions(
        self, document: ArtifactRegistryDocument
    ) -> dict[str, str]:
        active: dict[str, str] = {}
        for record in document.promotion_history:
            active[record.environment] = record.run_id
        return active

    def _with_derived_promotions(
        self, document: ArtifactRegistryDocument
    ) -> ArtifactRegistryDocument:
        active = self._active_promotions(document)
        updated_runs = [
            entry.model_copy(
                update={
                    "promoted_environments": [
                        environment
                        for environment, active_run_id in active.items()
                        if active_run_id == entry.run_id
                    ]
                }
            )
            for entry in document.runs
        ]
        return document.model_copy(update={"runs": updated_runs})

    def _run_is_listed(self, run_id: str) -> bool:
        return any(entry.run_id == run_id for entry in self._load_document().runs)


__all__ = [
    "ArtifactRegistry",
    "NoActiveModelError",
    "PromotionGateError",
    "RegistryError",
    "UnknownRunIdError",
]
