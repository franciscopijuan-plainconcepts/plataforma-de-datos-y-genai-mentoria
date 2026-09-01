"""MLOps contract models for training, registry, promotion, and inference."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


ModelName: TypeAlias = Literal["linear_regression", "catboost"]
EnvironmentName: TypeAlias = Literal["dev", "staging", "prod"]
PrimitiveValue: TypeAlias = str | int | float | bool


class SalesFeatureRow(BaseModel):
    """Typed feature row used by both training and inference."""

    model_config = ConfigDict(frozen=True)

    order_date: datetime
    order_dow: int = Field(ge=0, le=6)
    order_month: int = Field(ge=1, le=12)
    order_day_of_month: int = Field(ge=1, le=31)
    is_weekend: bool
    ship_mode: str
    segment: str
    region: str
    market: str
    product_id: str
    sub_category: str
    category: str
    quantity: int = Field(ge=0)
    has_discount: bool
    sales: Decimal | None = Field(default=None, ge=0)


class FeatureSet(BaseModel):
    """Deterministic extracted feature set with provenance metadata."""

    model_config = ConfigDict(frozen=True)

    rows: list[SalesFeatureRow]
    data_hash: str
    extracted_at: datetime
    source_table: Literal["Orders"]
    row_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_row_count(self) -> "FeatureSet":
        if self.row_count != len(self.rows):
            raise ValueError("row_count must equal len(rows)")
        return self


class ModelRunMetadata(BaseModel):
    """Metadata for one fitted model run."""

    model_config = ConfigDict(frozen=True)

    run_id: str = ""
    model_name: ModelName
    hyperparameters: dict[str, PrimitiveValue]
    library_versions: dict[str, str]
    data_hash: str
    trained_at: datetime
    train_row_count: int = Field(ge=0)
    test_row_count: int = Field(ge=0)
    split_cutoff_date: datetime
    artifact_path: str = ""
    training_duration_ms: int = Field(ge=0)


class EvaluationMetrics(BaseModel):
    """Evaluation metrics computed on the shared chronological test set."""

    model_config = ConfigDict(frozen=True)

    rmse: float = Field(ge=0)
    mae: float = Field(ge=0)
    r2: float
    test_row_count: int = Field(gt=0)
    split_cutoff_date: datetime


class ArtifactRegistryEntry(BaseModel):
    """Inspectable registry summary entry for a persisted run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    model_name: ModelName
    trained_at: datetime
    metrics: EvaluationMetrics
    promoted_environments: list[EnvironmentName] = Field(default_factory=list)


class PromotionRecord(BaseModel):
    """Immutable promotion event in the registry history."""

    model_config = ConfigDict(frozen=True)

    environment: EnvironmentName
    run_id: str
    promoted_at: datetime
    bypassed_staging_gate: bool = False

    @model_validator(mode="after")
    def _validate_bypass(self) -> "PromotionRecord":
        if self.bypassed_staging_gate and self.environment != "prod":
            raise ValueError(
                "bypassed_staging_gate can only be true for prod promotions"
            )
        return self


class ArtifactRegistryDocument(BaseModel):
    """Top-level persisted registry manifest."""

    model_config = ConfigDict(frozen=True)

    version: str
    runs: list[ArtifactRegistryEntry] = Field(default_factory=list)
    promotion_history: list[PromotionRecord] = Field(default_factory=list)


class PredictionInput(BaseModel):
    """Typed input for predict-sales."""

    model_config = ConfigDict(frozen=True)

    order_date: datetime
    ship_mode: str
    segment: str
    region: str
    market: str
    product_id: str
    sub_category: str
    category: str
    quantity: int = Field(ge=0)
    discount: Decimal


class PredictionResult(BaseModel):
    """Typed prediction output returned by inference."""

    model_config = ConfigDict(frozen=True)

    predicted_sales: Decimal
    run_id: str
    model_name: ModelName
    environment: EnvironmentName
    used_fallback_encoding: bool
    latency_ms: int = Field(ge=0)


class PredictionParseResult(BaseModel):
    """Typed outcome of parsing a natural-language prediction request.

    Produced by `src.ai_engineering.nl_predict` when the LLM is asked to
    extract a `PredictionInput` from free text (v3.1 NL->predict-sales).
    Exactly one of `prediction_input`/`missing_fields` should be meaningful:
    when the LLM has enough information, `prediction_input` is populated and
    `missing_fields` is empty; otherwise `prediction_input` is None and
    `missing_fields` names what is required to complete the request.
    """

    model_config = ConfigDict(frozen=True)

    question: str
    prediction_input: PredictionInput | None = None
    missing_fields: list[str] = Field(default_factory=list)
    clarification: str | None = None
    raw_llm_output: str = ""


__all__ = [
    "ArtifactRegistryDocument",
    "ArtifactRegistryEntry",
    "EnvironmentName",
    "EvaluationMetrics",
    "FeatureSet",
    "ModelName",
    "ModelRunMetadata",
    "PredictionInput",
    "PredictionParseResult",
    "PredictionResult",
    "PrimitiveValue",
    "PromotionRecord",
    "SalesFeatureRow",
]
