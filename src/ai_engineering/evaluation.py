"""Result-based evaluation through the interactive governed execution boundary."""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable
import json
import logging
from pathlib import Path
import re

from src.ai_engineering.pipeline import TextToSqlPipeline
from src.contracts.text_to_sql import NLQuestion, QueryRow, SampleQuestion


_logger = logging.getLogger(__name__)


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().lower())


def _freeze(value: object) -> Hashable:
    if isinstance(value, list):
        return ("list", tuple(_freeze(item) for item in value))
    if isinstance(value, dict):
        return ("dict", tuple(sorted((key, _freeze(item)) for key, item in value.items())))
    if isinstance(value, Hashable):
        return (type(value).__qualname__, value)
    raise ValueError(f"Unsupported evaluation result type: {type(value).__qualname__}")


def _results(rows: list[QueryRow]) -> Counter[tuple[Hashable, ...]]:
    # Column aliases and row ordering are presentation, but column positions,
    # value types, multiplicity and null/empty shapes remain significant.
    return Counter(tuple(_freeze(value) for value in row.data.values()) for row in rows)


def run_evaluation(pipeline: TextToSqlPipeline, sample_path: Path) -> str:
    questions = [
        SampleQuestion.model_validate(item)
        for item in json.loads(sample_path.read_text(encoding="utf-8"))
    ]
    correct = 0
    text_matches = 0
    failed_ids: list[str] = []
    diagnostics: list[str] = []
    for question in questions:
        response = pipeline.run(NLQuestion(text=question.question))
        result = response.query_result
        if (response.error or not response.validation.accepted or result is None or result.error):
            failed_ids.append(question.id)
            diagnostics.append(
                f"{question.id}: {response.error or response.validation.reason or (result.error if result else 'no execution')}"
            )
            continue
        text_matches += int(_normalize_sql(response.generated_sql.sql) == question.expected_sql_normalized)
        try:
            reference = pipeline.execute_reference_sql(
                question.expected_sql or question.expected_sql_normalized,
            )
            equivalent = _results(result.rows) == _results(reference)
        except Exception as exc:
            _logger.error("Evaluation reference failed for %s: %s", question.id, exc)
            equivalent = False
            diagnostics.append(f"{question.id}: reference failed: {exc}")
        if equivalent:
            correct += 1
        else:
            failed_ids.append(question.id)
    summary = f"{correct} / {len(questions)} correct"
    summary += f"\nSQL text matches: {text_matches} / {len(questions)} (diagnostic only)"
    if failed_ids:
        summary += f"\nFailed: {', '.join(failed_ids)}"
    if diagnostics:
        summary += "\n" + "\n".join(diagnostics)
    return summary


__all__ = ["run_evaluation"]
