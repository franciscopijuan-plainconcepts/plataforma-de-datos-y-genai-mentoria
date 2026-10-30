"""Canonical model consumption replaces the retired Python definition builder."""

import json

import pytest

from src.contracts.semantic_layer import SemanticLayerDocument
from src.data_engineering.semantic_layer.ossie import conversion_report
from src.data_engineering.semantic_layer.render import render_json
from tests.semantic_fixtures import (
    _build_test_dictionary, _build_test_table_defs, _canonical_model_text,
    _load_test_semantic_layer,
)


@pytest.fixture()
def document() -> SemanticLayerDocument:
    return _load_test_semantic_layer()


def test_canonical_metrics(document: SemanticLayerDocument) -> None:
    assert {metric.name: metric.source_table for metric in document.metrics} == {
        "gross_sales": "Orders", "returned_amount": "Orders", "net_sales": "Orders",
        "return_rate": "Orders", "total_profit": "Orders", "net_profit": "Orders",
        "avg_order_value": "Orders", "order_count": "Orders",
        # v3.3: forecast + backtest accuracy (proposed by the doc -> Ossie agent).
        "predicted_sales": "Predictions", "avg_predicted_line_sales": "Predictions",
        "backtest_actual_sales": "Forecast Accuracy", "backtest_predicted_sales": "Forecast Accuracy",
        "forecast_abs_error": "Forecast Accuracy", "forecast_wape": "Forecast Accuracy",
        "forecast_bias": "Forecast Accuracy",
    }


def test_canonical_forecast_datasets(document: SemanticLayerDocument) -> None:
    assert {table.name for table in document.tables} == {
        "Orders", "Returns", "People", "Predictions", "Forecast Accuracy",
    }
    wape = next(metric for metric in document.metrics if metric.name == "forecast_wape")
    assert wape.formula_sql == 'SUM("Abs Error") / NULLIF(SUM("Actual Sales"), 0)'
    assert wape.aggregation == "RATIO"
    assert wape.derives_from == ["forecast_abs_error", "backtest_actual_sales"]


def test_canonical_dimensions(document: SemanticLayerDocument) -> None:
    assert {dimension.name for dimension in document.dimensions} == {
        "region", "country", "market", "segment", "category", "sub_category",
        "ship_mode", "order_priority", "order_date", "customer", "product",
    }


def test_compatibility_associations(document: SemanticLayerDocument) -> None:
    assert {relationship.name for relationship in document.relationships} == {
        "orders_to_returns", "orders_to_people_by_region",
    }
    returned = next(item for item in document.relationships if item.name == "orders_to_returns")
    assert (returned.from_table, returned.from_column, returned.to_table, returned.to_column) == (
        "Returns", "Order ID", "Orders", "Order ID",
    )


def test_missing_physical_column_rejected() -> None:
    tables = _build_test_table_defs()
    tables[0] = tables[0].model_copy(update={
        "columns": [column for column in tables[0].columns if column.name != "Profit"],
    })
    with pytest.raises(ValueError, match=r"Orders\.profit"):
        _load_test_semantic_layer(_build_test_dictionary(tables))


def test_canonical_file_provenance(document: SemanticLayerDocument) -> None:
    assert document.provenance is not None
    assert document.provenance.model_sha256 == conversion_report(_canonical_model_text()).model_sha256
    assert document.provenance.source_sha256 == document.source_sha256
    assert document.provenance.semantic_source_sha256 == document.semantic_source_sha256


def test_derivative_json_is_deterministic() -> None:
    assert render_json(_load_test_semantic_layer()) == render_json(_load_test_semantic_layer())


def test_json_excludes_generated_at(document: SemanticLayerDocument) -> None:
    assert "generated_at" not in json.loads(render_json(document))


def test_json_contains_provenance(document: SemanticLayerDocument) -> None:
    raw = json.loads(render_json(document))
    assert raw["source_sha256"] == document.source_sha256
    assert raw["semantic_source_sha256"] == document.semantic_source_sha256
    assert raw["provenance"]["model_sha256"] == conversion_report(_canonical_model_text()).model_sha256
