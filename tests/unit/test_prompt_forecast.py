"""v3.3: forecast metrics and the accuracy view are rendered without being mistaken for Orders."""

from src.ai_engineering.prompt_builder import build_prompt
from src.contracts.data_access import ColumnDef, LogicalType, TableDef
from src.contracts.semantic_layer import Metric
from src.contracts.text_to_sql import NLQuestion
from tests.semantic_fixtures import _build_test_dictionary, _build_test_table_defs, _load_test_semantic_layer


_ACCURACY = TableDef(name="Forecast Accuracy", description="Backtest accuracy.", columns=[
    ColumnDef(name="Region", logical_type=LogicalType.STRING, nullable=False),
    ColumnDef(name="Abs Error", logical_type=LogicalType.DECIMAL, nullable=False),
])


def _prompt(extra_tables: dict[str, TableDef] | None = None) -> str:
    tables = _build_test_table_defs()
    dictionary = _build_test_dictionary(tables)
    layer = _load_test_semantic_layer(dictionary)
    layer = layer.model_copy(update={"metrics": [*layer.metrics, Metric(
        name="forecast_abs_error", formula_sql='SUM("Abs Error")', business_description="Abs error.",
        source_table="Forecast Accuracy", aggregation="SUM",
    )]})
    return build_prompt(NLQuestion(text="model error by region"), dictionary, tables[0], layer, extra_tables)


def test_non_orders_metric_is_tagged_with_its_source() -> None:
    prompt = _prompt()
    assert '- forecast_abs_error [FROM "Forecast Accuracy"] = SUM("Abs Error")' in prompt
    assert "- gross_sales = SUM(\"Sales\")" in prompt


def test_accuracy_rule_only_when_view_is_queryable() -> None:
    assert 'query "Forecast Accuracy" alone' not in _prompt()
    assert 'query "Forecast Accuracy" alone' in _prompt({"forecast accuracy": _ACCURACY})
