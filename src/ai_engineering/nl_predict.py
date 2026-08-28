"""Natural-language -> sales-prediction request parsing (v3.1).

Turns a free-text question (e.g. "What sales would I get for a Consumer
order shipped Second Class to the West region, market US, product
TEC-AC-10003033, sub-category Accessories, category Technology, quantity 3,
no discount, ordered on 2024-06-01?") into a typed `PredictionInput` that can
be fed straight into `src.mlops.inference.predict_sales`.

Design notes:
- The LLM is instructed to respond with STRICT JSON only (no markdown), the
  same pattern used by `prompt_builder.py`/`llm_client.py` for SQL
  generation, rather than OpenAI "tools"/function-calling, because the
  underlying Forge-proxied model is addressed like a plain chat model.
- Known categorical vocabularies observed by the *promoted* model (e.g. the
  known `Region`/`Segment`/`Ship Mode` values) are injected into the prompt
  so the LLM maps the user's wording onto categories the model actually
  knows, and can fall back to the closest match instead of hallucinating.
- This module does not call the model, the registry, or any I/O — it only
  builds prompts and parses/validates the LLM's JSON reply. Orchestration
  (loading the active model, calling `predict_sales`) lives in the CLI, same
  separation-of-concerns as `TextToSqlPipeline` vs. `QueryProvider`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from src.ai_engineering.llm_protocol import TextCompleter
from src.contracts.mlops import PredictionInput, PredictionParseResult
from src.contracts.text_to_sql import NLQuestion

_logger = logging.getLogger(__name__)

_REQUIRED_FIELDS: tuple[str, ...] = (
    "order_date",
    "ship_mode",
    "segment",
    "region",
    "market",
    "product_id",
    "sub_category",
    "category",
    "quantity",
    "discount",
)

_SYSTEM_INSTRUCTION = (
    "You are a data assistant that extracts structured sales-prediction "
    "inputs from a natural-language question. You do NOT compute the "
    "prediction yourself — you only extract the parameters needed to call "
    "a trained regression model."
)


def _render_known_categories(known_categories: dict[str, list[str]]) -> str:
    lines: list[str] = ["Known categorical values observed during training:"]
    for field_name in ("ship_mode", "segment", "region", "market", "sub_category", "category"):
        values = known_categories.get(field_name, [])
        if not values:
            continue
        shown = values if len(values) <= 40 else [*values[:40], "..."]
        lines.append(f"- {field_name}: {shown}")
    lines.append(
        "- product_id: a free-text product code (e.g. 'TEC-AC-10003033'). If the "
        "user does not name one, pick the most plausible code style or ask for it "
        "via missing_fields."
    )
    return "\n".join(lines)


def build_prediction_prompt(
    question: NLQuestion,
    known_categories: dict[str, list[str]],
    reference_date: datetime | None = None,
) -> str:
    """Build the LLM prompt that extracts a `PredictionInput` from `question`."""
    today = (reference_date or datetime.now(timezone.utc)).date().isoformat()
    schema = (
        "{\n"
        '  "status": "ok" | "missing_information",\n'
        '  "prediction_input": {\n'
        '    "order_date": "YYYY-MM-DD",\n'
        '    "ship_mode": string,\n'
        '    "segment": string,\n'
        '    "region": string,\n'
        '    "market": string,\n'
        '    "product_id": string,\n'
        '    "sub_category": string,\n'
        '    "category": string,\n'
        '    "quantity": integer,\n'
        '    "discount": number (0.0-1.0)\n'
        "  },\n"
        '  "missing_fields": [string, ...],\n'
        '  "clarification": string\n'
        "}"
    )
    return (
        f"{_SYSTEM_INSTRUCTION}\n\n"
        f"Today's date (use this if the question omits an order date): {today}\n\n"
        f"{_render_known_categories(known_categories)}\n\n"
        "Output STRICT JSON only (no markdown fences, no explanations) matching "
        f"this schema:\n{schema}\n\n"
        "Rules:\n"
        '- If you can confidently fill every field, set status to "ok" and '
        'populate "prediction_input" fully; leave "missing_fields" empty.\n'
        '- If required information is missing and cannot be reasonably inferred '
        '(e.g. no product/category mentioned at all), set status to '
        '"missing_information", list the missing field names in '
        '"missing_fields", and write a short one-sentence "clarification" asking '
        "the user for them. Do not include \"prediction_input\" in that case.\n"
        "- discount must be a fraction between 0.0 and 1.0 (e.g. 10% -> 0.1).\n"
        "- quantity must be a positive integer.\n\n"
        f"User question: {question.text}"
    )


def parse_prediction_response(question: NLQuestion, raw_output: str) -> PredictionParseResult:
    """Parse the LLM's raw text reply into a typed `PredictionParseResult`."""
    payload = _extract_json_object(raw_output)
    if payload is None:
        return PredictionParseResult(
            question=question.text,
            missing_fields=list(_REQUIRED_FIELDS),
            clarification="The assistant did not return valid JSON.",
            raw_llm_output=raw_output,
        )

    status = payload.get("status")
    if status == "ok" and isinstance(payload.get("prediction_input"), dict):
        try:
            prediction_input = PredictionInput.model_validate(payload["prediction_input"])
        except ValidationError as exc:
            _logger.info("LLM prediction_input failed validation: %s", exc)
            return PredictionParseResult(
                question=question.text,
                missing_fields=_missing_from_payload(payload.get("prediction_input", {})),
                clarification=f"Extracted fields did not validate: {exc}",
                raw_llm_output=raw_output,
            )
        return PredictionParseResult(
            question=question.text,
            prediction_input=prediction_input,
            raw_llm_output=raw_output,
        )

    missing = payload.get("missing_fields")
    missing_fields = (
        [str(f) for f in missing] if isinstance(missing, list) and missing else list(_REQUIRED_FIELDS)
    )
    clarification = payload.get("clarification")
    return PredictionParseResult(
        question=question.text,
        missing_fields=missing_fields,
        clarification=str(clarification) if clarification else None,
        raw_llm_output=raw_output,
    )


def _missing_from_payload(candidate: dict[str, Any]) -> list[str]:
    return [name for name in _REQUIRED_FIELDS if name not in candidate or candidate[name] in (None, "")]


def _extract_json_object(raw_output: str) -> dict[str, Any] | None:
    text = raw_output.strip()
    if text.startswith("```"):
        # Strip markdown fences like ```json ... ``` defensively — some
        # models add them even when instructed not to.
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


class PredictSalesAssistant:
    """Orchestrates prompt-building + LLM call + parsing for NL->predict-sales."""

    def __init__(self, llm_client: TextCompleter, known_categories: dict[str, list[str]]) -> None:
        self._llm_client = llm_client
        self._known_categories = known_categories

    def parse(self, question: NLQuestion) -> PredictionParseResult:
        prompt = build_prediction_prompt(question, self._known_categories)
        try:
            raw_output = self._llm_client.complete(prompt)
        except Exception as exc:  # pragma: no cover - defensive network path
            _logger.error("LLM call failed for NL->predict-sales: %s", exc)
            return PredictionParseResult(
                question=question.text,
                missing_fields=list(_REQUIRED_FIELDS),
                clarification=f"LLM call failed: {exc}",
            )
        return parse_prediction_response(question, raw_output)


__all__ = [
    "PredictSalesAssistant",
    "build_prediction_prompt",
    "parse_prediction_response",
]
