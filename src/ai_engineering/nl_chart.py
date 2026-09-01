"""Natural-language -> chart specification parsing (v3.1).

Given a natural-language question (e.g. "plot total sales by region as a
bar chart") and the column names available in a `QueryResult` (produced by
the existing Text-to-SQL pipeline), asks the LLM to choose a chart type and
which columns map to the X/Y axes, then validates the answer into a typed
`ChartSpec`.

Rendering itself (matplotlib) happens in `src/reporting/chart_renderer.py` —
this module only builds prompts and parses/validates the LLM's JSON reply,
mirroring `nl_predict.py` and the existing `prompt_builder.py` separation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from src.ai_engineering.llm_protocol import TextCompleter
from src.contracts.charting import ChartParseResult, ChartSpec
from src.contracts.text_to_sql import NLQuestion

_logger = logging.getLogger(__name__)

_SYSTEM_INSTRUCTION = (
    "You are a data-visualization assistant. Given a user's natural-language "
    "charting request and the columns available in the query result, choose "
    "the best chart type and axis mapping."
)


def build_chart_prompt(question: NLQuestion, columns: list[str]) -> str:
    """Build the LLM prompt that derives a `ChartSpec` from `question` + `columns`."""
    schema = (
        "{\n"
        '  "chart_type": "bar" | "line" | "scatter",\n'
        '  "x_field": string (must be one of the available columns),\n'
        '  "y_field": string (must be one of the available columns),\n'
        '  "title": string,\n'
        '  "aggregation": "sum" | "avg" | "count" | "none"\n'
        "}"
    )
    return (
        f"{_SYSTEM_INSTRUCTION}\n\n"
        f"Available columns: {columns}\n\n"
        "Guidance:\n"
        "- Use 'line' for time-series/date-like X fields, 'bar' for categorical "
        "comparisons, 'scatter' for two numeric fields.\n"
        "- x_field and y_field MUST be chosen from the available columns list "
        "exactly as spelled (case-sensitive).\n"
        "- If multiple rows share the same x_field value, set 'aggregation' to "
        "how they should be combined ('sum' for totals like Sales, 'avg' for "
        "rates, 'count' for occurrences); use 'none' only when rows are already "
        "one-per-x-value.\n\n"
        "Output STRICT JSON only (no markdown fences, no explanations) matching "
        f"this schema:\n{schema}\n\n"
        f"User request: {question.text}"
    )


def parse_chart_response(
    question: NLQuestion, raw_output: str, columns: list[str]
) -> ChartParseResult:
    """Parse the LLM's raw text reply into a typed `ChartParseResult`."""
    payload = _extract_json_object(raw_output)
    if payload is None:
        return ChartParseResult(
            question=question.text,
            error="The assistant did not return valid JSON.",
            raw_llm_output=raw_output,
        )
    try:
        spec = ChartSpec.model_validate(payload)
    except ValidationError as exc:
        return ChartParseResult(
            question=question.text,
            error=f"Chart spec failed validation: {exc}",
            raw_llm_output=raw_output,
        )
    if spec.x_field not in columns or spec.y_field not in columns:
        return ChartParseResult(
            question=question.text,
            error=(
                f"Chart spec references unknown columns (x={spec.x_field!r}, "
                f"y={spec.y_field!r}); available columns: {columns}"
            ),
            raw_llm_output=raw_output,
        )
    return ChartParseResult(question=question.text, spec=spec, raw_llm_output=raw_output)


def _extract_json_object(raw_output: str) -> dict[str, Any] | None:
    text = raw_output.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    return parsed


class ChartSpecAssistant:
    """Orchestrates prompt-building + LLM call + parsing for NL->chart."""

    def __init__(self, llm_client: TextCompleter) -> None:
        self._llm_client = llm_client

    def parse(self, question: NLQuestion, columns: list[str]) -> ChartParseResult:
        prompt = build_chart_prompt(question, columns)
        try:
            raw_output = self._llm_client.complete(prompt)
        except Exception as exc:  # pragma: no cover - defensive network path
            _logger.error("LLM call failed for NL->chart: %s", exc)
            return ChartParseResult(question=question.text, error=f"LLM call failed: {exc}")
        return parse_chart_response(question, raw_output, columns)


__all__ = [
    "ChartSpecAssistant",
    "build_chart_prompt",
    "parse_chart_response",
]
