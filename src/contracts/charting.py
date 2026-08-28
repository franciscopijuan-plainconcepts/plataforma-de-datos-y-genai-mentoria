"""Chart contract models (v3.1 NL -> chart).

These models are the typed currency between the AI Engineering domain
(which turns a natural-language request + the available result columns into
a `ChartSpec`) and the `src/reporting` domain (which renders the spec +
`QueryResult` rows into a PNG file with matplotlib).

Keeping this as its own contracts module (rather than folding it into
`text_to_sql.py` or `mlops.py`) mirrors the existing pattern: each domain
boundary gets its own typed, frozen Pydantic v2 contract file.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

ChartType: TypeAlias = Literal["bar", "line", "scatter"]


class ChartSpec(BaseModel):
    """LLM-derived specification of how to render a chart from query results."""

    model_config = ConfigDict(frozen=True)

    chart_type: ChartType
    x_field: str = Field(min_length=1)
    y_field: str = Field(min_length=1)
    title: str = Field(min_length=1)
    aggregation: Literal["sum", "avg", "count", "none"] = "none"


class ChartParseResult(BaseModel):
    """Outcome of asking the LLM to derive a `ChartSpec` from NL + columns."""

    model_config = ConfigDict(frozen=True)

    question: str
    spec: ChartSpec | None = None
    error: str | None = None
    raw_llm_output: str = ""


class ChartResult(BaseModel):
    """Result of rendering a chart to disk."""

    model_config = ConfigDict(frozen=True)

    spec: ChartSpec
    image_path: str
    point_count: int = Field(ge=0)


__all__ = [
    "ChartType",
    "ChartSpec",
    "ChartParseResult",
    "ChartResult",
]
