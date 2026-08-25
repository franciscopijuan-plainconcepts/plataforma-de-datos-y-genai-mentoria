"""Contract tests for MLOps domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import get_args, get_origin

import pytest
from pydantic import ValidationError

from src.contracts.mlops import (
    ArtifactRegistryDocument,
    ArtifactRegistryEntry,
    EvaluationMetrics,
    FeatureSet,
    ModelRunMetadata,
    PredictionInput,
    PredictionResult,
    PromotionRecord,
    SalesFeatureRow,
)


_NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def _sample_feature_row(*, sales: Decimal | None = Decimal("42.50")) -> SalesFeatureRow:
    return SalesFeatureRow(
        order_date=_NOW,
        order_dow=0,
        order_month=8,
        order_day_of_month=25,
        is_weekend=False,
        ship_mode="Second Class",
        segment="Consumer",
        region="West",
        market="US",
        product_id="TEC-AC-10003033",
        sub_category="Accessories",
        category="Technology",
        quantity=3,
        has_discount=False,
        sales=sales,
    )


@pytest.mark.parametrize(
    "model_cls",
    [
        SalesFeatureRow,
        FeatureSet,
        ModelRunMetadata,
        EvaluationMetrics,
        ArtifactRegistryEntry,
        PromotionRecord,
        ArtifactRegistryDocument,
        PredictionInput,
        PredictionResult,
    ],
)
def test_all_mlops_models_are_frozen(model_cls: type[object]) -> None:
    assert getattr(model_cls, "model_config", {}).get("frozen") is True


def test_sales_feature_row_is_immutable() -> None:
    row = _sample_feature_row()
    with pytest.raises(ValidationError):
        row.quantity = 99


def test_sales_feature_row_validates_ranges() -> None:
    valid = _sample_feature_row()
    assert valid.order_dow == 0
    assert valid.quantity == 3

    with pytest.raises(ValidationError):
        SalesFeatureRow.model_validate({**valid.model_dump(), "order_dow": 7})
    with pytest.raises(ValidationError):
        SalesFeatureRow.model_validate({**valid.model_dump(), "quantity": -1})
    with pytest.raises(ValidationError):
        SalesFeatureRow.model_validate({**valid.model_dump(), "sales": Decimal("-1")})


def test_feature_set_row_count_invariant() -> None:
    feature_set = FeatureSet(
        rows=[_sample_feature_row()],
        data_hash="abc",
        extracted_at=_NOW,
        source_table="Orders",
        row_count=1,
    )
    assert feature_set.row_count == 1

    with pytest.raises(ValidationError):
        FeatureSet(
            rows=[_sample_feature_row()],
            data_hash="abc",
            extracted_at=_NOW,
            source_table="Orders",
            row_count=2,
        )


def test_evaluation_metrics_validate_non_negative_errors() -> None:
    EvaluationMetrics(
        rmse=1.2,
        mae=0.8,
        r2=-0.5,
        test_row_count=10,
        split_cutoff_date=_NOW,
    )
    with pytest.raises(ValidationError):
        EvaluationMetrics(
            rmse=-1.0,
            mae=0.8,
            r2=0.1,
            test_row_count=10,
            split_cutoff_date=_NOW,
        )


def test_promotion_record_bypass_only_valid_for_prod() -> None:
    prod_record = PromotionRecord(
        environment="prod",
        run_id="run-1",
        promoted_at=_NOW,
        bypassed_staging_gate=True,
    )
    assert prod_record.bypassed_staging_gate is True

    with pytest.raises(ValidationError):
        PromotionRecord(
            environment="staging",
            run_id="run-1",
            promoted_at=_NOW,
            bypassed_staging_gate=True,
        )


def test_registry_document_round_trips_through_json() -> None:
    metrics = EvaluationMetrics(
        rmse=1.2,
        mae=0.8,
        r2=0.5,
        test_row_count=10,
        split_cutoff_date=_NOW,
    )
    entry = ArtifactRegistryEntry(
        run_id="run-1",
        model_name="catboost",
        trained_at=_NOW,
        metrics=metrics,
        promoted_environments=["dev"],
    )
    document = ArtifactRegistryDocument(
        version="1.0.0",
        runs=[entry],
        promotion_history=[
            PromotionRecord(environment="dev", run_id="run-1", promoted_at=_NOW)
        ],
    )

    payload = document.model_dump_json()
    reloaded = ArtifactRegistryDocument.model_validate_json(payload)
    assert reloaded == document


def test_prediction_models_validate() -> None:
    prediction_input = PredictionInput(
        order_date=_NOW,
        ship_mode="Second Class",
        segment="Consumer",
        region="West",
        market="US",
        product_id="TEC-AC-10003033",
        sub_category="Accessories",
        category="Technology",
        quantity=1,
        discount=Decimal("0.0"),
    )
    assert prediction_input.quantity == 1

    with pytest.raises(ValidationError):
        PredictionInput.model_validate({**prediction_input.model_dump(), "quantity": -1})

    result = PredictionResult(
        predicted_sales=Decimal("99.99"),
        run_id="run-1",
        model_name="linear_regression",
        environment="dev",
        used_fallback_encoding=False,
        latency_ms=12,
    )
    assert result.environment == "dev"


def test_contract_models_expose_explicit_field_types() -> None:
    sales_field = SalesFeatureRow.model_fields["sales"]
    assert sales_field.annotation == Decimal | None

    promoted_field = ArtifactRegistryEntry.model_fields["promoted_environments"]
    assert get_origin(promoted_field.annotation) is list
    assert get_args(promoted_field.annotation) == (get_args(promoted_field.annotation)[0],)

    run_id_field = ModelRunMetadata.model_fields["run_id"]
    assert run_id_field.annotation is str
