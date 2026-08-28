"""Chart rendering (v3.1): `QueryResult` + `ChartSpec` -> PNG file.

The ONLY module allowed to import `matplotlib` (see boundary contract test
`tests/contract/test_boundaries.py::test_matplotlib_confined_to_reporting`).
Pure rendering: no LLM calls, no SQL execution, no MLOps model loading — it
only turns already-typed data into an image on disk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless backend — no display server required (CLI/CI safe)
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set before this import)

from src.contracts.charting import ChartResult, ChartSpec
from src.contracts.text_to_sql import QueryResult

_DEFAULT_OUTPUT_DIR = Path(".artifacts/charts")


class ChartRenderError(RuntimeError):
    """Raised when a chart cannot be rendered from the given spec/result."""


def render_chart(
    query_result: QueryResult,
    spec: ChartSpec,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
) -> ChartResult:
    """Render `query_result` rows according to `spec` and save a PNG.

    Rows are aggregated by `spec.x_field` per `spec.aggregation` ("sum",
    "avg", "count", or "none" when each x value already appears once).
    Raises `ChartRenderError` for empty/failed query results or when no
    numeric data points can be extracted for the requested fields.
    """
    if query_result.error:
        raise ChartRenderError(f"Cannot chart a failed query result: {query_result.error}")
    if not query_result.rows:
        raise ChartRenderError("Cannot chart an empty query result (0 rows).")

    x_values, y_values = _aggregate(query_result, spec)
    if not x_values:
        raise ChartRenderError(
            f"No usable numeric data points for x_field={spec.x_field!r}, "
            f"y_field={spec.y_field!r}."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    image_path = output_dir / f"chart-{timestamp}.png"

    fig, ax = plt.subplots(figsize=(9, 5))
    try:
        labels = [str(v) for v in x_values]
        if spec.chart_type == "bar":
            ax.bar(labels, y_values)
        elif spec.chart_type == "line":
            ax.plot(labels, y_values, marker="o")
        else:  # scatter
            ax.scatter(range(len(x_values)), y_values)
            ax.set_xticks(range(len(x_values)))
            ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_title(spec.title)
        ax.set_xlabel(spec.x_field)
        ax.set_ylabel(spec.y_field)
        fig.autofmt_xdate(rotation=45)
        fig.tight_layout()
        fig.savefig(image_path)
    finally:
        plt.close(fig)

    return ChartResult(spec=spec, image_path=str(image_path), point_count=len(x_values))


def _aggregate(query_result: QueryResult, spec: ChartSpec) -> tuple[list[Any], list[float]]:
    """Group rows by `x_field` and combine `y_field` per `spec.aggregation`."""
    grouped: dict[Any, list[float]] = {}
    order: list[Any] = []
    for row in query_result.rows:
        if spec.x_field not in row.data:
            continue
        y_numeric = 1.0 if spec.aggregation == "count" else _to_float(row.data.get(spec.y_field))
        if y_numeric is None:
            continue
        x_key = row.data[spec.x_field]
        if x_key not in grouped:
            grouped[x_key] = []
            order.append(x_key)
        grouped[x_key].append(y_numeric)

    aggregated_x: list[Any] = []
    aggregated_y: list[float] = []
    for x_key in order:
        values = grouped[x_key]
        if spec.aggregation == "sum":
            aggregated_y.append(sum(values))
        elif spec.aggregation == "avg":
            aggregated_y.append(sum(values) / len(values))
        elif spec.aggregation == "count":
            aggregated_y.append(float(len(values)))
        else:  # "none" — one value expected per x; use the first if duplicated
            aggregated_y.append(values[0])
        aggregated_x.append(x_key)
    return aggregated_x, aggregated_y


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["ChartRenderError", "render_chart"]
