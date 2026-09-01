"""Unit tests for shared MLOps feature engineering."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.contracts.mlops import PredictionInput
from src.mlops.features import derive_prediction_row, derive_training_row
from tests.unit._mlops_support import sample_order_row


def test_derive_training_row_populates_temporal_features_for_weekday() -> None:
    order = sample_order_row(order_date=datetime(2024, 8, 19, tzinfo=timezone.utc))
    row = derive_training_row(order)
    assert row.order_dow == 0
    assert row.order_month == 8
    assert row.order_day_of_month == 19
    assert row.is_weekend is False
    assert row.sales == Decimal("100.00")


def test_derive_training_row_marks_weekend_correctly() -> None:
    saturday = derive_training_row(
        sample_order_row(order_date=datetime(2024, 8, 17, tzinfo=timezone.utc))
    )
    sunday = derive_training_row(
        sample_order_row(order_date=datetime(2024, 8, 18, tzinfo=timezone.utc))
    )
    assert saturday.is_weekend is True
    assert sunday.is_weekend is True


def test_negative_discount_logs_and_maps_to_false(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        row = derive_training_row(
            sample_order_row(
                order_date=datetime(2024, 8, 19, tzinfo=timezone.utc),
                discount=Decimal("-0.15"),
            )
        )
    assert row.has_discount is False
    assert "Negative discount" in caplog.text


def test_training_and_prediction_derivation_share_same_feature_logic() -> None:
    order_date = datetime(2024, 8, 17, tzinfo=timezone.utc)
    training_row = derive_training_row(
        sample_order_row(
            order_date=order_date,
            discount=Decimal("0.25"),
            quantity=4,
        )
    )
    prediction_row = derive_prediction_row(
        PredictionInput(
            order_date=order_date,
            ship_mode="Second Class",
            segment="Consumer",
            region="West",
            market="US",
            product_id="TEC-AC-10003033",
            sub_category="Accessories",
            category="Technology",
            quantity=4,
            discount=Decimal("0.25"),
        )
    )
    assert prediction_row.order_dow == training_row.order_dow
    assert prediction_row.order_month == training_row.order_month
    assert prediction_row.order_day_of_month == training_row.order_day_of_month
    assert prediction_row.is_weekend == training_row.is_weekend
    assert prediction_row.has_discount == training_row.has_discount
    assert prediction_row.sales is None
