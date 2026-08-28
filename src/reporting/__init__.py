"""Reporting domain (v3.1): renders `QueryResult` + `ChartSpec` into PNG charts.

This is the ONLY module allowed to import `matplotlib` (enforced by the
boundary contract test, mirroring how `pandas`/`psycopg`/`openai` are each
confined to a single domain per the constitution).
"""

from __future__ import annotations

from src.reporting.chart_renderer import render_chart

__all__ = ["render_chart"]
