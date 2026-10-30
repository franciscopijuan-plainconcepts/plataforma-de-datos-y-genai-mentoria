"""Predictive dashboard card specs, fed from the canonical Ossie metrics (v3.3).

Pure: builds the SQL of each card by looking up metric formulas BY NAME in the
loaded `SemanticLayerDocument`, so the dashboard always shows what the
semantic model defines (a renamed or removed metric fails loudly here instead
of silently drifting). The SQL is ungoverned on purpose — the CLI composition
root runs every statement through `GovernedQueryProvider.prepare_query` (RLS)
before publishing it to Metabase.

Forecasts follow the wall clock (`seed_predictions`), while Orders history
ends in 2017, so history and forecast are separate cards rather than one
series with a multi-year gap.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.metabase import CardDisplay
from src.contracts.semantic_layer import SemanticLayerDocument


_NEXT_MONTH = "date_trunc('month', CURRENT_DATE + INTERVAL '1 month')"
_MONTH_AFTER = "date_trunc('month', CURRENT_DATE + INTERVAL '2 month')"
_ORDER_MONTH = "date_trunc('month', \"Order Date\")"


class ForecastCardSpec(BaseModel):
    """One card of the predictive dashboard: query, chart and grid position."""

    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    sql: str
    display: CardDisplay
    visualization_settings: dict[str, object] = {}
    row: int = Field(ge=0)
    col: int = Field(ge=0, le=23)
    size_x: int = Field(ge=1, le=24)
    size_y: int = Field(ge=1)


def _formula(layer: SemanticLayerDocument, name: str, source_table: str) -> str:
    metric = next((item for item in layer.metrics if item.name == name), None)
    if metric is None:
        raise ValueError(f"Forecast dashboard needs metric {name!r} in the semantic model")
    if metric.source_table != source_table:
        raise ValueError(f"Metric {name!r} is defined on {metric.source_table}, expected {source_table}")
    return metric.formula_sql


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _line(dimension: str, *metrics: str) -> dict[str, object]:
    return {"graph.dimensions": [dimension], "graph.metrics": list(metrics)}


def _percent(column: str) -> dict[str, object]:
    key = json.dumps(["name", column], separators=(",", ":"))
    return {"column_settings": {key: {"number_style": "percent", "decimals": 1}}}


def build_forecast_cards(
    layer: SemanticLayerDocument, *, run_id: str, environment: str
) -> list[ForecastCardSpec]:
    """Cards for the run active in `environment` (forecast + its backtest)."""
    gross_sales = _formula(layer, "gross_sales", "Orders")
    avg_predicted = _formula(layer, "avg_predicted_line_sales", "Predictions")
    actual = _formula(layer, "backtest_actual_sales", "Forecast Accuracy")
    predicted = _formula(layer, "backtest_predicted_sales", "Forecast Accuracy")
    wape = _formula(layer, "forecast_wape", "Forecast Accuracy")
    bias = _formula(layer, "forecast_bias", "Forecast Accuracy")

    forecast_scope = f'"Environment" = {_literal(environment)} AND "Run ID" = {_literal(run_id)}'
    backtest_scope = f'"Run ID" = {_literal(run_id)}'
    return [
        ForecastCardSpec(
            key="next_month", name="Forecast próximo mes — ticket medio por línea",
            sql=(f'SELECT {avg_predicted} AS "avg_predicted_line_sales" FROM "Predictions" '
                 f'WHERE {forecast_scope} AND "Order Date" >= {_NEXT_MONTH} '
                 f'AND "Order Date" < {_MONTH_AFTER}'),
            display="scalar", row=0, col=0, size_x=8, size_y=4,
        ),
        ForecastCardSpec(
            key="wape", name="Error del modelo (WAPE, backtest)",
            sql=f'SELECT {wape} AS "forecast_wape" FROM "Forecast Accuracy" WHERE {backtest_scope}',
            display="scalar", visualization_settings=_percent("forecast_wape"),
            row=0, col=8, size_x=8, size_y=4,
        ),
        ForecastCardSpec(
            key="bias", name="Sesgo del forecast (backtest)",
            sql=f'SELECT {bias} AS "forecast_bias" FROM "Forecast Accuracy" WHERE {backtest_scope}',
            display="scalar", visualization_settings=_percent("forecast_bias"),
            row=0, col=16, size_x=8, size_y=4,
        ),
        ForecastCardSpec(
            key="history", name="Ventas mensuales — histórico",
            sql=(f'SELECT {_ORDER_MONTH} AS "Month", {gross_sales} AS "gross_sales" FROM "Orders" '
                 f'GROUP BY {_ORDER_MONTH} ORDER BY "Month"'),
            display="line", visualization_settings=_line("Month", "gross_sales"),
            row=4, col=0, size_x=24, size_y=8,
        ),
        ForecastCardSpec(
            key="forecast_months", name="Forecast próximos meses — ticket medio por línea",
            sql=(f'SELECT {_ORDER_MONTH} AS "Month", {avg_predicted} AS "avg_predicted_line_sales" '
                 f'FROM "Predictions" WHERE {forecast_scope} AND "Order Date" >= {_NEXT_MONTH} '
                 f'GROUP BY {_ORDER_MONTH} ORDER BY "Month"'),
            display="bar", visualization_settings=_line("Month", "avg_predicted_line_sales"),
            row=12, col=0, size_x=12, size_y=8,
        ),
        ForecastCardSpec(
            key="backtest", name="Backtest: ventas previstas vs reales por mes",
            sql=(f'SELECT "Month", {actual} AS "backtest_actual_sales", '
                 f'{predicted} AS "backtest_predicted_sales" FROM "Forecast Accuracy" '
                 f'WHERE {backtest_scope} GROUP BY "Month" ORDER BY "Month"'),
            display="line",
            visualization_settings=_line("Month", "backtest_actual_sales", "backtest_predicted_sales"),
            row=12, col=12, size_x=12, size_y=8,
        ),
    ]


__all__ = ["ForecastCardSpec", "build_forecast_cards"]
