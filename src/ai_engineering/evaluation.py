"""Sanity-check evaluation harness (v1.1).

Runs a small set of sample questions through the full Text-to-SQL pipeline
and compares the generated SQL (normalized) to the expected SQL. Prints a
simple pass/fail summary — no per-failure diagnostics or configurable modes.

Reference: specs/002-text-to-sql-v1/research.md Part E
            specs/002-text-to-sql-v1/contracts/text_to_sql.md
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.ai_engineering.pipeline import TextToSqlPipeline
from src.contracts.text_to_sql import SampleQuestion


def _normalize_sql(sql: str) -> str:
    """Normalize SQL for comparison: lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", sql.strip().lower())


def run_evaluation(
    pipeline: TextToSqlPipeline, sample_path: Path
) -> str:
    """Run the sanity-check evaluation and return a summary string.

    Loads sample questions from `sample_path`, runs each through the pipeline,
    normalizes the generated SQL, and compares to the expected SQL.
    Prints `X / N correct` + failed question IDs.
    """
    questions_data = json.loads(sample_path.read_text(encoding="utf-8"))
    questions = [SampleQuestion(**q) for q in questions_data]

    correct = 0
    failed_ids: list[str] = []

    for q in questions:
        from src.contracts.text_to_sql import NLQuestion

        response = pipeline.run(NLQuestion(text=q.question))

        if response.error:
            failed_ids.append(q.id)
            continue

        generated = _normalize_sql(response.generated_sql.sql)
        expected = q.expected_sql_normalized

        if generated == expected:
            correct += 1
        else:
            failed_ids.append(q.id)

    total = len(questions)
    summary = f"{correct} / {total} correct"
    if failed_ids:
        summary += f"\nFailed: {', '.join(failed_ids)}"
    return summary


__all__ = ["run_evaluation"]
