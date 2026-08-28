"""Unit tests for NL -> prediction-request parsing (v3.1)."""

from __future__ import annotations

import json

from src.ai_engineering.nl_predict import (
    PredictSalesAssistant,
    build_prediction_prompt,
    parse_prediction_response,
)
from src.contracts.text_to_sql import NLQuestion


class _FakeLlm:
    """Fake `TextCompleter` returning a canned response for tests."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self._response


_OK_PAYLOAD = {
    "status": "ok",
    "prediction_input": {
        "order_date": "2024-06-01",
        "ship_mode": "Second Class",
        "segment": "Consumer",
        "region": "West",
        "market": "US",
        "product_id": "TEC-AC-10003033",
        "sub_category": "Accessories",
        "category": "Technology",
        "quantity": 3,
        "discount": 0.1,
    },
    "missing_fields": [],
    "clarification": "",
}


def test_build_prediction_prompt_includes_question_and_known_categories() -> None:
    prompt = build_prediction_prompt(
        NLQuestion(text="Predict sales for a Consumer order in the West region"),
        known_categories={"region": ["West", "East"], "segment": ["Consumer"]},
    )
    assert "Predict sales for a Consumer order in the West region" in prompt
    assert "West" in prompt
    assert "Consumer" in prompt


def test_parse_prediction_response_extracts_valid_input() -> None:
    result = parse_prediction_response(
        NLQuestion(text="predict"), json.dumps(_OK_PAYLOAD)
    )
    assert result.prediction_input is not None
    assert result.prediction_input.region == "West"
    assert result.prediction_input.quantity == 3
    assert not result.missing_fields


def test_parse_prediction_response_handles_missing_information() -> None:
    payload = {
        "status": "missing_information",
        "missing_fields": ["product_id", "region"],
        "clarification": "Which product and region?",
    }
    result = parse_prediction_response(NLQuestion(text="predict"), json.dumps(payload))
    assert result.prediction_input is None
    assert result.missing_fields == ["product_id", "region"]
    assert result.clarification == "Which product and region?"


def test_parse_prediction_response_handles_invalid_json() -> None:
    result = parse_prediction_response(NLQuestion(text="predict"), "not json at all")
    assert result.prediction_input is None
    assert result.missing_fields
    assert result.clarification is not None


def test_parse_prediction_response_strips_markdown_fences() -> None:
    fenced = "```json\n" + json.dumps(_OK_PAYLOAD) + "\n```"
    result = parse_prediction_response(NLQuestion(text="predict"), fenced)
    assert result.prediction_input is not None
    assert result.prediction_input.product_id == "TEC-AC-10003033"


def test_predict_sales_assistant_end_to_end_with_fake_llm() -> None:
    fake_llm = _FakeLlm(json.dumps(_OK_PAYLOAD))
    assistant = PredictSalesAssistant(
        llm_client=fake_llm,
        known_categories={"region": ["West"], "segment": ["Consumer"]},
    )
    result = assistant.parse(NLQuestion(text="Predict sales for a West region order"))
    assert result.prediction_input is not None
    assert fake_llm.last_prompt is not None
    assert "Predict sales for a West region order" in fake_llm.last_prompt
