"""Shared feature derivation utilities for training and inference.

Feature selection rationale (per `data_dictionary.md` cardinalities, EDA on
the 51290-row `Orders` table):

- `City` (3650 unique), `State` (1106 unique), `Country` (165 unique) are
  DROPPED. Product pricing in this dataset is not localized (no evidence in
  the data dictionary of city/state/country-level price variation), so these
  fields carry no direct causal signal on `Sales`. `Region` (23) and `Market`
  (5) already encode the same geographic hierarchy at a generalizable
  granularity (same anchor used by the v2.0 Semantic Layer's RLS). Keeping
  City/State/Country in addition to Region/Market would only add redundant,
  near-duplicate, high-cardinality dimensions that inflate the one-hot /
  frequency-encoding space for the linear model and dilute per-category
  sample counts for both models, without adding real predictive information.
- `Product Name` is DROPPED. Its cardinality (3788) is IDENTICAL to
  `Product ID` (3788) in the data dictionary — a bijective, purely redundant
  identifier pair for the same entity (free-text label vs. code). Keeping
  both would double the encoding dimensionality for zero incremental signal.
  `Product ID` is kept as the canonical identifier (proxy for unit price).
- `Quantity` and `has_discount` (derived from `Discount`) are KEPT: both are
  legitimate, order-time-known predictors of `Sales` (not derived FROM
  `Sales`, so no label leakage), unlike `Profit`/`Shipping Cost` which are
  intentionally excluded because they are computed FROM `Sales` downstream.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.contracts.data_access import OrderRow
from src.contracts.mlops import PredictionInput, SalesFeatureRow


LOGGER = logging.getLogger(__name__)

MODEL_FEATURE_FIELDS: tuple[str, ...] = (
    "order_dow",
    "order_month",
    "order_day_of_month",
    "is_weekend",
    "ship_mode",
    "segment",
    "region",
    "market",
    "product_id",
    "sub_category",
    "category",
    "quantity",
    "has_discount",
)

LOW_CARDINALITY_FIELDS: tuple[str, ...] = (
    "order_dow",
    "order_month",
    "order_day_of_month",
    "is_weekend",
    "ship_mode",
    "segment",
    "region",
    "market",
    "sub_category",
    "category",
    "has_discount",
)

HIGH_CARDINALITY_FIELDS: tuple[str, ...] = (
    "product_id",
)

NUMERIC_FIELDS: tuple[str, ...] = ("quantity",)

CATEGORICAL_FIELDS: tuple[str, ...] = (
    "ship_mode",
    "segment",
    "region",
    "market",
    "product_id",
    "sub_category",
    "category",
)

CATBOOST_FEATURE_FIELDS: tuple[str, ...] = MODEL_FEATURE_FIELDS
CATBOOST_CATEGORICAL_FIELDS: tuple[str, ...] = CATEGORICAL_FIELDS


def _derive_temporal_features(order_date: datetime) -> tuple[int, int, int, bool]:
    order_dow = order_date.weekday()
    order_month = order_date.month
    order_day_of_month = order_date.day
    is_weekend = order_dow >= 5
    return order_dow, order_month, order_day_of_month, is_weekend


def _derive_has_discount(discount: Decimal) -> bool:
    if discount < Decimal("0"):
        LOGGER.warning(
            "Negative discount encountered during feature derivation: %s", discount
        )
        return False
    return discount > Decimal("0")


def derive_training_row(order: OrderRow) -> SalesFeatureRow:
    """Derive a typed training row from an Orders row."""
    return _build_sales_feature_row(
        order_date=order.order_date,
        ship_mode=order.ship_mode,
        segment=order.segment,
        region=order.region,
        market=order.market,
        product_id=order.product_id,
        sub_category=order.sub_category,
        category=order.category,
        quantity=order.quantity,
        discount=order.discount,
        sales=order.sales,
    )


def derive_prediction_row(input_row: PredictionInput) -> SalesFeatureRow:
    """Derive a typed inference row from CLI prediction input."""
    return _build_sales_feature_row(
        order_date=input_row.order_date,
        ship_mode=input_row.ship_mode,
        segment=input_row.segment,
        region=input_row.region,
        market=input_row.market,
        product_id=input_row.product_id,
        sub_category=input_row.sub_category,
        category=input_row.category,
        quantity=input_row.quantity,
        discount=input_row.discount,
        sales=None,
    )


def _build_sales_feature_row(
    *,
    order_date: datetime,
    ship_mode: str,
    segment: str,
    region: str,
    market: str,
    product_id: str,
    sub_category: str,
    category: str,
    quantity: int,
    discount: Decimal,
    sales: Decimal | None,
) -> SalesFeatureRow:
    order_dow, order_month, order_day_of_month, is_weekend = _derive_temporal_features(
        order_date
    )
    return SalesFeatureRow(
        order_date=order_date,
        order_dow=order_dow,
        order_month=order_month,
        order_day_of_month=order_day_of_month,
        is_weekend=is_weekend,
        ship_mode=ship_mode,
        segment=segment,
        region=region,
        market=market,
        product_id=product_id,
        sub_category=sub_category,
        category=category,
        quantity=quantity,
        has_discount=_derive_has_discount(discount),
        sales=sales,
    )


def build_feature_vector(
    row: SalesFeatureRow,
    feature_fields: tuple[str, ...] = MODEL_FEATURE_FIELDS,
) -> list[object]:
    """Return a stable feature vector in the configured field order."""
    return [getattr(row, field_name) for field_name in feature_fields]


def categorical_vocabulary(rows: list[SalesFeatureRow]) -> dict[str, list[str]]:
    """Return the observed categorical values for fallback detection."""
    return {
        field_name: sorted(
            {str(getattr(row, field_name)) for row in rows}
        )
        for field_name in CATEGORICAL_FIELDS
    }


def stable_row_sort_key(row: SalesFeatureRow) -> tuple[str, str]:
    """Deterministic row-ordering key for hashing and extraction."""
    canonical_payload = json.dumps(
        row.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return row.order_date.isoformat(), canonical_payload


def stable_row_payload(row: SalesFeatureRow) -> dict[str, Any]:
    """JSON-safe payload used in deterministic dataset hashing."""
    return row.model_dump(mode="json")


__all__ = [
    "CATBOOST_CATEGORICAL_FIELDS",
    "CATBOOST_FEATURE_FIELDS",
    "CATEGORICAL_FIELDS",
    "HIGH_CARDINALITY_FIELDS",
    "LOW_CARDINALITY_FIELDS",
    "MODEL_FEATURE_FIELDS",
    "NUMERIC_FIELDS",
    "build_feature_vector",
    "categorical_vocabulary",
    "derive_prediction_row",
    "derive_training_row",
    "stable_row_payload",
    "stable_row_sort_key",
]
