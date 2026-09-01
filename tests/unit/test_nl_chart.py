"""Unit tests for NL -> chart-spec parsing (v3.1)."""

from __future__ import annotations

import json

from src.ai_engineering.nl_chart import (
    ChartSpecAssistant,
    build_chart_prompt,
    parse_chart_response,
)
from src.contracts.text_to_sql import NLQuestion


class _FakeLlm:
    def __init__(self, response: str) -> None:
        self._response = response
        self.last_prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self._response


def test_build_chart_prompt_lists_available_columns() -> None:
    prompt = build_chart_prompt(
        NLQuestion(text="plot total sales by region"), columns=["region", "total_sales"]
    )
    assert "region" in prompt
    assert "total_sales" in prompt
    assert "plot total sales by region" in prompt


def test_parse_chart_response_extracts_valid_spec() -> None:
    payload = {
        "chart_type": "bar",
        "x_field": "region",
        "y_field": "total_sales",
        "title": "Total sales by region",
        "aggregation": "sum",
    }
    result = parse_chart_response(
        NLQuestion(text="plot sales by region"),
        json.dumps(payload),
        columns=["region", "total_sales"],
    )
    assert result.spec is not None
    assert result.spec.chart_type == "bar"
    assert result.spec.x_field == "region"
    assert result.error is None


def test_parse_chart_response_rejects_unknown_columns() -> None:
    payload = {
        "chart_type": "bar",
        "x_field": "not_a_real_column",
        "y_field": "total_sales",
        "title": "Bad chart",
        "aggregation": "sum",
    }
    result = parse_chart_response(
        NLQuestion(text="plot sales"),
        json.dumps(payload),
        columns=["region", "total_sales"],
    )
    assert result.spec is None
    assert result.error is not None
    assert "not_a_real_column" in result.error


def test_parse_chart_response_handles_invalid_json() -> None:
    result = parse_chart_response(
        NLQuestion(text="plot sales"), "definitely not json", columns=["region"]
    )
    assert result.spec is None
    assert result.error is not None


def test_chart_spec_assistant_end_to_end_with_fake_llm() -> None:
    payload = {
        "chart_type": "line",
        "x_field": "order_month",
        "y_field": "total_sales",
        "title": "Sales over time",
        "aggregation": "sum",
    }
    fake_llm = _FakeLlm(json.dumps(payload))
    assistant = ChartSpecAssistant(llm_client=fake_llm)
    result = assistant.parse(
        NLQuestion(text="plot sales over time"),
        columns=["order_month", "total_sales"],
    )
    assert result.spec is not None
    assert result.spec.chart_type == "line"
    assert fake_llm.last_prompt is not None
