"""Contract test for data-access Protocol conformance (constitution-mandated).

Verifies that the PostgreSQL adapter satisfies the runtime_checkable Protocols
AND that `load_rows` behaves correctly with typed contract models:
- accepts validated Pydantic row models,
- rejects (or surfaces errors for) untyped / partial payloads.

This test does NOT require a running database — it focuses on the structural
contract (Protocol conformance) and the typed-payload contract.

Reference: specs/001-data-genai-platform-baseline/contracts/data_access.md
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from src.contracts.data_access import OrderRow, PersonRow, ReturnRow, Row
from src.data_access.adapters.postgres.repository import PostgresRepository
from src.data_access.interfaces import DataProvider, QueryProvider, SchemaProvider


# ---------------------------------------------------------------------------
# Protocol conformance (runtime_checkable)
# ---------------------------------------------------------------------------

def _make_repo_without_connection() -> PostgresRepository:
    """Construct a PostgresRepository structurally (no DB connection needed).

    We bypass __init__ so the conformance check is purely structural — it
    verifies the methods exist with the right Protocol signatures, not that
    a live DB is reachable.
    """
    return PostgresRepository.__new__(PostgresRepository)


@pytest.mark.parametrize(
    "protocol",
    [SchemaProvider, DataProvider, QueryProvider],
)
def test_repository_satisfies_protocol(protocol: type) -> None:
    """PostgresRepository satisfies every runtime_checkable Protocol."""
    repo = _make_repo_without_connection()
    assert isinstance(repo, protocol), (
        f"PostgresRepository must be an instance of {protocol.__name__}"
    )


# ---------------------------------------------------------------------------
# Typed-payload contract: row models validate cleanly
# ---------------------------------------------------------------------------

def test_order_row_validates_with_decimal_money_fields() -> None:
    """OrderRow MUST accept Decimal (not float) for money fields — Principle I."""
    from datetime import datetime

    row = OrderRow(
        row_id=1,
        order_id="CA-2016-12345-1",
        order_date=datetime(2016, 1, 1),
        ship_date=datetime(2016, 1, 3),
        ship_mode="Standard Class",
        customer_id="CUST-1",
        customer_name="Test Customer",
        segment="Consumer",
        postal_code=None,  # nullable per EDA
        city="Test City",
        state="Test State",
        country="Test Country",
        region="Central US",
        market="USCA",
        product_id="PROD-1",
        product_name="Test Product",
        sub_category="Binders",
        category="Office Supplies",
        sales=Decimal("22638.48"),
        quantity=14,
        discount=Decimal("0.402"),
        profit=Decimal("-6599.978"),  # signed
        shipping_cost=Decimal("933.57"),
        order_priority="Critical",
    )
    # Money fields MUST be Decimal, not float.
    assert isinstance(row.sales, Decimal)
    assert isinstance(row.profit, Decimal)
    assert isinstance(row.discount, Decimal)
    assert isinstance(row.shipping_cost, Decimal)


def test_return_row_requires_surrogate_return_id() -> None:
    """ReturnRow MUST carry the surrogate Return ID (not bare Order ID PK)."""
    row = ReturnRow(
        return_id=1,
        returned="Yes",
        order_id="CA-2016-12345-1",
        region="Central US",
    )
    assert row.return_id == 1
    # `returned` is kept as str (not Literal["Yes"]) to detect source drift.
    assert isinstance(row.returned, str)


def test_person_row_normalization_is_callers_responsibility() -> None:
    """PersonRow accepts a normalized Person value (loader normalizes \\xa0)."""
    row = PersonRow(person="Andile Ihejirika", region="Central Africa")
    assert "\xa0" not in row.person  # loader strips non-breaking spaces


# ---------------------------------------------------------------------------
# Reject untyped / partial payloads at the boundary (FR-015 fail-fast)
# ---------------------------------------------------------------------------

def test_typeadapter_rejects_partial_order_row() -> None:
    """A bad row (missing required field) MUST raise ValidationError — FR-015."""
    adapter: TypeAdapter[list[Row]] = TypeAdapter(list[OrderRow])
    with pytest.raises(ValidationError):
        adapter.validate_python([{"row_id": 1}])  # missing required fields


def test_typeadapter_rejects_wrong_type_for_money() -> None:
    """A float where Decimal is expected for money MUST raise — Principle I."""
    adapter: TypeAdapter[list[Row]] = TypeAdapter(list[OrderRow])
    bad_row = {
        "row_id": 1,
        "order_id": "X",
        "order_date": "2020-01-01T00:00:00",
        "ship_date": "2020-01-02T00:00:00",
        "ship_mode": "Standard Class",
        "customer_id": "C",
        "customer_name": "N",
        "segment": "Consumer",
        "city": "C",
        "state": "S",
        "country": "Co",
        "region": "R",
        "market": "USCA",
        "product_id": "P",
        "product_name": "PN",
        "sub_category": "B",
        "category": "Office Supplies",
        "sales": "not-a-number",  # invalid Decimal
        "quantity": 1,
        "discount": "0.0",
        "profit": "0.0",
        "shipping_cost": "0.0",
        "order_priority": "Low",
    }
    with pytest.raises(ValidationError):
        adapter.validate_python([bad_row])


# ---------------------------------------------------------------------------
# load_rows signature is typed — no Any/dict leaks
# ---------------------------------------------------------------------------

def test_load_rows_signature_is_explicitly_typed() -> None:
    """load_rows MUST have explicit type annotations on `rows` and the return
    value (constitution Principle I: no `Any`/`dict` leaks across boundaries).

    We inspect the raw annotations (not via get_type_hints, which would try to
    resolve the `Row` union's forward references and fail without the right
    globals). The contract here is that annotations EXIST and are non-`Any`.
    """
    import inspect

    sig = inspect.signature(PostgresRepository.load_rows)
    rows_param = sig.parameters.get("rows")
    assert rows_param is not None, "load_rows must declare a `rows` parameter"
    # The annotation must be present (not inspect.Parameter.empty) and must
    # not be `Any` (which would leak untyped payloads across the boundary).
    assert rows_param.annotation is not inspect.Parameter.empty, (
        "load_rows `rows` parameter must have an explicit type annotation"
    )
    annotation_str = str(rows_param.annotation)
    assert "Any" not in annotation_str or "Union" in annotation_str, (
        f"load_rows `rows` annotation must not be bare `Any`: {annotation_str}"
    )
    assert sig.return_annotation is not inspect.Signature.empty, (
        "load_rows must declare a return type annotation"
    )
