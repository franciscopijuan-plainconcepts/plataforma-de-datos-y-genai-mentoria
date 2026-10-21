"""SQL text equality cannot conceal rejected or failed executions."""

from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.ai_engineering.evaluation import run_evaluation
from src.ai_engineering.pipeline import TextToSqlPipeline
from src.contracts.text_to_sql import QueryRow, ValidationResult
from tests.unit.test_cli_governed_output import response


def samples(tmp_path: Path) -> Path:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps([{
        "id": "q01", "question": "sales",
        "expected_sql_normalized": 'select sum("sales") from "orders"',
        "expected_sql": 'SELECT SUM("Sales") FROM "Orders"',
    }]), encoding="utf-8")
    return path


def test_different_sql_same_results_passes(tmp_path: Path) -> None:
    candidate = response()
    assert candidate.query_result is not None
    candidate = candidate.model_copy(update={
        "generated_sql": candidate.generated_sql.model_copy(update={"sql": "SELECT 150 AS different_alias"}),
        "query_result": candidate.query_result.model_copy(update={"rows": [QueryRow(data={"different_alias": Decimal("150")})]}),
    })
    pipeline = Mock(spec=TextToSqlPipeline)
    pipeline.run.return_value = candidate
    pipeline.execute_reference_sql.return_value = [QueryRow(data={"sum": Decimal("150")})]
    summary = run_evaluation(pipeline, samples(tmp_path))
    assert summary.startswith("1 / 1 correct")
    assert "SQL text matches: 0 / 1" in summary
    pipeline.execute_reference_sql.assert_called_once_with('SELECT SUM("Sales") FROM "Orders"')


@pytest.mark.parametrize("failure", ["validation", "execution", "reference", "types", "cardinality"])
def test_text_match_is_insufficient(tmp_path: Path, failure: str) -> None:
    candidate = response()
    assert candidate.query_result is not None
    candidate = candidate.model_copy(update={
        "query_result": candidate.query_result.model_copy(update={"rows": [QueryRow(data={"sum": Decimal("150")})]}),
    })
    if failure == "validation":
        candidate = candidate.model_copy(update={"validation": ValidationResult(accepted=False, sql="", reason="Rejected")})
    elif failure == "execution":
        assert candidate.query_result is not None
        candidate = candidate.model_copy(update={"query_result": candidate.query_result.model_copy(update={"error": "failed"})})
    pipeline = Mock(spec=TextToSqlPipeline)
    pipeline.run.return_value = candidate
    pipeline.execute_reference_sql.return_value = [QueryRow(data={"sum": Decimal("150")})]
    if failure == "reference":
        pipeline.execute_reference_sql.side_effect = RuntimeError("Reference failed")
    elif failure == "types":
        pipeline.execute_reference_sql.return_value = [QueryRow(data={"sum": 150})]
    elif failure == "cardinality":
        pipeline.execute_reference_sql.return_value = [
            QueryRow(data={"sum": Decimal("150")}), QueryRow(data={"sum": Decimal("150")}),
        ]
    summary = run_evaluation(pipeline, samples(tmp_path))
    assert summary.startswith("0 / 1 correct") and "Failed: q01" in summary
    if failure in {"validation", "execution"}:
        pipeline.execute_reference_sql.assert_not_called()
