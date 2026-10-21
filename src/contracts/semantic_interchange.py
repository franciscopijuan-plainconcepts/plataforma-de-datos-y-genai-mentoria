"""Platform metadata carried in a versioned Ossie compatibility extension."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from src.contracts.semantic_layer import (
    DimensionType,
    MetricAggregation,
    SemanticRelationship,
    TableType,
)


class CompatibilityModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    profile_version: Literal[1] = 1


class ModelCompatibility(CompatibilityModel):
    content_version: str
    source_sha256: str
    semantic_source_sha256: str
    assumptions: list[str]
    associations: list[SemanticRelationship]
    required_profile: Literal["PLATAFORMA_DATOS_GENAI"] = "PLATAFORMA_DATOS_GENAI"

    @field_validator("associations", mode="before")
    @classmethod
    def reject_unknown_association_properties(cls, value: object) -> object:
        if isinstance(value, list):
            for association in value:
                if isinstance(association, dict):
                    unknown = association.keys() - SemanticRelationship.model_fields.keys()
                    if unknown:
                        raise ValueError(f"Unknown association properties: {sorted(unknown)}")
        return value


class DatasetCompatibility(CompatibilityModel):
    table_type: TableType


class FieldCompatibility(CompatibilityModel):
    physical_column: str
    dimension_type: DimensionType | None = None


class MetricCompatibility(CompatibilityModel):
    source_table: Literal["Orders"]
    aggregation: MetricAggregation
    derives_from: list[str]
    uses_returns: bool
    assumption: str | None
    execution_dialect: Literal["postgres"] = "postgres"
    portability: Literal["platform-profile"] = "platform-profile"


class ConversionIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entity: str
    portability: Literal["platform-profile", "standard"]
    reason: str


class ConversionReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ossie_revision: str
    issues: list[ConversionIssue]
    model_sha256: str
