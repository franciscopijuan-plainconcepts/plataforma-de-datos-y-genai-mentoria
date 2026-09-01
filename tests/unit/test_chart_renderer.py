"""Unit tests for chart rendering (v3.1)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.contracts.charting import ChartSpec
from src.contracts.text_to_sql import QueryResult, QueryRow
from src.reporting.chart_renderer import ChartRenderError, render_chart

_OUTPUT_DIR = Path(".artifacts/charts-tests")


def setup_module() -> None:
    if _OUTPUT_DIR.exists():
        shutil.rmtree(_OUTPUT_DIR)


def teardown_module() -> None:
    if _OUTPUT_DIR.exists():
        shutil.rmtree(_OUTPUT_DIR)


def _result(rows: list[dict[str, object]]) -> QueryResult:
    return QueryResult(
        sql="SELECT 1",
        rows=[QueryRow(data=r) for r in rows],
        row_count=len(rows),
        latency_ms=1,
        error=None,
    )


def test_render_chart_bar_with_sum_aggregation_creates_png() -> None:
    query_result = _result(
        [
            {"region": "West", "sales": 100},
            {"region": "West", "sales": 50},
            {"region": "East", "sales": 30},
        ]
    )
    spec = ChartSpec(
        chart_type="bar",
        x_field="region",
        y_field="sales",
        title="Sales by region",
        aggregation="sum",
    )
    result = render_chart(query_result, spec, output_dir=_OUTPUT_DIR)
    assert Path(result.image_path).exists()
    assert Path(result.image_path).stat().st_size > 0
    assert result.point_count == 2  # West, East


def test_render_chart_line_no_aggregation() -> None:
    query_result = _result(
        [
            {"month": "Jan", "sales": 10},
            {"month": "Feb", "sales": 20},
        ]
    )
    spec = ChartSpec(
        chart_type="line", x_field="month", y_field="sales", title="Monthly sales"
    )
    result = render_chart(query_result, spec, output_dir=_OUTPUT_DIR)
    assert result.point_count == 2


def test_render_chart_rejects_empty_result() -> None:
    empty_result = _result([])
    spec = ChartSpec(chart_type="bar", x_field="region", y_field="sales", title="t")
    with pytest.raises(ChartRenderError):
        render_chart(empty_result, spec, output_dir=_OUTPUT_DIR)


def test_render_chart_rejects_failed_query_result() -> None:
    failed_result = QueryResult(
        sql="SELECT 1", rows=[], row_count=0, latency_ms=1, error="boom"
    )
    spec = ChartSpec(chart_type="bar", x_field="region", y_field="sales", title="t")
    with pytest.raises(ChartRenderError):
        render_chart(failed_result, spec, output_dir=_OUTPUT_DIR)


def test_render_chart_count_aggregation() -> None:
    query_result = _result(
        [
            {"region": "West", "sales": None},
            {"region": "West", "sales": None},
            {"region": "East", "sales": None},
        ]
    )
    spec = ChartSpec(
        chart_type="bar",
        x_field="region",
        y_field="sales",
        title="Order counts",
        aggregation="count",
    )
    result = render_chart(query_result, spec, output_dir=_OUTPUT_DIR)
    assert result.point_count == 2
