"""Structured CLI/bridge output must carry successful effective SQL only."""

from contextlib import contextmanager
from collections.abc import Iterator
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

import ask_metabase as bridge
from src.cli import main as cli
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import (
    GeneratedSql, NLQuestion, QueryResult, TextToSqlResponse, ValidationResult,
)


def response() -> TextToSqlResponse:
    return TextToSqlResponse(
        question=NLQuestion(text="sales"),
        generated_sql=GeneratedSql(sql='SELECT SUM("Sales") FROM "Orders"', model_name="test", raw_response={}),
        validation=ValidationResult(accepted=True, sql="generated"),
        query_result=QueryResult(
            sql='SELECT SUM("Sales") FROM "Orders" WHERE "Region" IN (\'North\')',
            rows=[], row_count=0, latency_ms=1, model_sha256="model-sha",
        ),
    )


def test_ask_json_has_no_progress_in_stdout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    expected = response()

    def run(*args: object, **kwargs: object) -> tuple[TextToSqlResponse, SemanticViewer]:
        print("Progress message")
        return expected, SemanticViewer(viewer_id="north", regions=["North"])

    monkeypatch.setattr(cli, "_run_ask_pipeline", run)
    cli.main(["ask", "sales", "--viewer", "north", "--json", "--no-metabase"])
    captured = capsys.readouterr()
    restored = TextToSqlResponse.model_validate_json(captured.out)
    assert restored == expected
    assert "Progress message" in captured.err


@pytest.mark.parametrize("failure", ["rejected", "execution", "missing", "top-level"])
def test_json_errors_have_nonzero_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    failed = response()
    if failure == "rejected":
        failed = failed.model_copy(update={"validation": ValidationResult(accepted=False, sql="", reason="Rejected")})
    elif failure == "execution":
        assert failed.query_result is not None
        failed = failed.model_copy(update={"query_result": failed.query_result.model_copy(update={"error": "failed"})})
    elif failure == "missing":
        failed = failed.model_copy(update={"query_result": None})
    else:
        failed = failed.model_copy(update={"error": "failed"})
    monkeypatch.setattr(cli, "_run_ask_pipeline", Mock(return_value=(failed, SemanticViewer(viewer_id="north", regions=[]))))
    with pytest.raises(SystemExit) as exc:
        cli.main(["ask", "sales", "--json", "--no-metabase"])
    assert exc.value.code == 1
    assert TextToSqlResponse.model_validate_json(capsys.readouterr().out) == failed


def test_bridge_uses_effective_sql_without_integrated_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = response()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, expected.model_dump_json(), "progress"))
    monkeypatch.setattr("ask_metabase.subprocess.run", runner)
    effective = bridge.ask_pipeline_for_sql("sales", "north")
    assert expected.query_result is not None
    assert effective == expected.query_result.sql and effective != expected.generated_sql.sql
    command = runner.call_args.args[0]
    assert "--json" in command and "--no-metabase" in command
    runner.assert_called_once()


@pytest.mark.parametrize("stdout,returncode", [
    ("not json", 0),
    (json.dumps({"error": "not authorized"}), 1),
    (response().model_copy(update={"query_result": None}).model_dump_json(), 0),
])
def test_bridge_failures_do_not_retry_or_publish(
    monkeypatch: pytest.MonkeyPatch, stdout: str, returncode: int,
) -> None:
    runner = Mock(return_value=subprocess.CompletedProcess([], returncode, stdout, "diagnostic"))
    publish = Mock()
    monkeypatch.setattr("ask_metabase.subprocess.run", runner)
    monkeypatch.setattr(bridge, "create_metabase_question", publish)
    monkeypatch.setattr(bridge, "METABASE_API_KEY", "unit-test")
    monkeypatch.setattr(sys, "argv", ["ask_metabase.py", "sales", "--viewer", "north"])
    with pytest.raises(SystemExit):
        bridge.main()
    runner.assert_called_once()
    publish.assert_not_called()


def test_evaluation_uses_shared_composition(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sample_dir = tmp_path / "specs" / "002-text-to-sql-v1"
    sample_dir.mkdir(parents=True)
    (sample_dir / "sample_questions.json").write_text("[]")
    pipeline = Mock()
    composed = Mock()

    @contextmanager
    def compose(*args: object, **kwargs: object) -> Iterator[tuple[Mock, SemanticViewer]]:
        composed(*args, **kwargs)
        yield pipeline, SemanticViewer(viewer_id="north", regions=["North"])

    evaluate = Mock(return_value="0 / 0 correct")
    monkeypatch.setattr(cli, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "_compose_ask_pipeline", compose)
    monkeypatch.setattr("src.ai_engineering.evaluation.run_evaluation", evaluate)
    cli.main(["evaluate", "--viewer", "north"])
    composed.assert_called_once_with("north", False, metabase_enabled=False)
    evaluate.assert_called_once_with(pipeline, sample_dir / "sample_questions.json")
