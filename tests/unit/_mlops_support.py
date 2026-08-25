"""Shared helpers for MLOps unit tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from src.contracts.data_access import OrderRow
from src.contracts.mlops import SalesFeatureRow
from src.mlops.features import derive_training_row


def sample_order_row(
    *,
    order_date: datetime,
    discount: Decimal = Decimal("0.0"),
    product_id: str = "TEC-AC-10003033",
    product_name: str = "Logitech P710e Mobile Speakerphone",
    city: str = "Los Angeles",
    state: str = "California",
    sales: Decimal = Decimal("100.00"),
    quantity: int = 2,
) -> OrderRow:
    return OrderRow(
        row_id=1,
        order_id="CA-2016-152156",
        order_date=order_date,
        ship_date=order_date + timedelta(days=2),
        ship_mode="Second Class",
        customer_id="CG-12520",
        customer_name="Claire Gute",
        segment="Consumer",
        postal_code="90001",
        city=city,
        state=state,
        country="United States",
        region="West",
        market="US",
        product_id=product_id,
        product_name=product_name,
        sub_category="Accessories",
        category="Technology",
        sales=sales,
        quantity=quantity,
        discount=discount,
        profit=Decimal("10.00"),
        shipping_cost=Decimal("5.00"),
        order_priority="High",
    )


def sample_training_rows() -> list[SalesFeatureRow]:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows: list[SalesFeatureRow] = []
    for index in range(20):
        rows.append(
            derive_training_row(
                sample_order_row(
                    order_date=base + timedelta(days=index),
                    discount=Decimal("0.10") if index % 3 == 0 else Decimal("0.0"),
                    product_id=f"SKU-{index % 4}",
                    product_name=f"Product {index % 5}",
                    city=["Los Angeles", "Seattle", "Austin"][index % 3],
                    state=["California", "Washington", "Texas"][index % 3],
                    sales=Decimal(str(100 + index * 7)),
                    quantity=(index % 5) + 1,
                )
            )
        )
    return rows


def registry_test_root(name: str) -> Path:
    return Path('.artifacts') / name
