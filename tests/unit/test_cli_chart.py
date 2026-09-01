"""CLI-level unit tests for the `chart` command (v3.1 NL -> chart).

These tests monkeypatch `src.cli.main._run_ask_pipeline` so the chart command
can be exercised without Docker/PostgreSQL — the shared ask-pipeline plumbing
(viewer resolution, RLS, SQL generation/execution) is already covered by the
existing `ask` integration tests; here we only need to verify `cmd_chart`'s
own orchestration: error propagation for each failure path, and the success
path wiring into `ChartSpecAssistant` + `render_chart`.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from src.cli.main import main
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import (
    GeneratedSql,
    NLQuestion,
    QueryResult,
    QueryRow,
    TextToSqlResponse,
    ValidationResult,
)

_VIEWER = SemanticViewer(
    viewer_id="full_access_local_dev", regions=[], allows_full_access=True, is_local_dev=True
)
_CHART_OUTPUT_DIR = Path(".artifacts/charts")

_AskPipelineFn = Callable[[str, "str | None", bool], "tuple[TextToSqlResponse, SemanticViewer]"]


def _fake_ask_pipeline(response: TextToSqlResponse) -> _AskPipelineFn:
    def _run(question: str, viewer_id: str | None, allow_full_access: bool) -> tuple[TextToSqlResponse, SemanticViewer]:
        del question, viewer_id, allow_full_access
        return response, _VIEWER

    return _run


def _accepted_response(query_result: QueryResult | None, error: str | None = None) -> TextToSqlResponse:
    return TextToSqlResponse(
        question=NLQuestion(text="plot total sales by region"),
        generated_sql=GeneratedSql(sql="SELECT 1", model_name="test-model", raw_response={}),
        validation=ValidationResult(accepted=True, reason=None, sql="SELECT 1"),
        query_result=query_result,
        error=error,
    )


def teardown_module() -> None:
    if _CHART_OUTPUT_DIR.exists():
        shutil.rmtree(_CHART_OUTPUT_DIR)


def test_chart_fails_when_pipeline_reports_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    response = _accepted_response(query_result=None, error="LLM call failed: boom")
    monkeypatch.setattr("src.cli.main._run_ask_pipeline", _fake_ask_pipeline(response))
    with pytest.raises(SystemExit) as exc_info:
        main(["chart", "plot total sales by region"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Text-to-SQL step failed" in captured.err


def test_chart_fails_when_sql_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    response = TextToSqlResponse(
        question=NLQuestion(text="plot total sales by region"),
        generated_sql=GeneratedSql(sql="DELETE FROM Orders", model_name="test-model", raw_response={}),
        validation=ValidationResult(accepted=False, reason="not a SELECT", sql="DELETE FROM Orders"),
        query_result=None,
    )
    monkeypatch.setattr("src.cli.main._run_ask_pipeline", _fake_ask_pipeline(response))
    with pytest.raises(SystemExit) as exc_info:
        main(["chart", "plot total sales by region"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Generated SQL was rejected" in captured.err


def test_chart_fails_when_query_result_has_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    query_result = QueryResult(sql="SELECT 1", rows=[], row_count=0, latency_ms=1, error="db exploded")
    response = _accepted_response(query_result=query_result)
    monkeypatch.setattr("src.cli.main._run_ask_pipeline", _fake_ask_pipeline(response))
    with pytest.raises(SystemExit) as exc_info:
        main(["chart", "plot total sales by region"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Query execution failed" in captured.err


def test_chart_fails_when_query_result_is_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    query_result = QueryResult(sql="SELECT 1", rows=[], row_count=0, latency_ms=1, error=None)
    response = _accepted_response(query_result=query_result)
    monkeypatch.setattr("src.cli.main._run_ask_pipeline", _fake_ask_pipeline(response))
    with pytest.raises(SystemExit) as exc_info:
        main(["chart", "plot total sales by region"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "0 rows" in captured.err


def test_chart_fails_when_llm_cannot_derive_spec(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    query_result = QueryResult(
        sql="SELECT 1",
        rows=[QueryRow(data={"region": "West", "total_sales": 100})],
        row_count=1,
        latency_ms=1,
        error=None,
    )
    response = _accepted_response(query_result=query_result)
    monkeypatch.setattr("src.cli.main._run_ask_pipeline", _fake_ask_pipeline(response))
    monkeypatch.setenv("FORGE_API_KEY", "test-key")

    class _FailingChartLlm:
        def __init__(self, config: object) -> None:
            del config

        def complete(self, prompt: str) -> str:
            del prompt
            return "not valid json"

    monkeypatch.setattr("src.ai_engineering.llm_client.LlmClient", _FailingChartLlm)
    with pytest.raises(SystemExit) as exc_info:
        main(["chart", "plot total sales by region"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Could not derive a chart specification" in captured.err


def test_chart_succeeds_and_renders_png(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    query_result = QueryResult(
        sql="SELECT 1",
        rows=[
            QueryRow(data={"region": "West", "total_sales": 100}),
            QueryRow(data={"region": "East", "total_sales": 50}),
        ],
        row_count=2,
        latency_ms=1,
        error=None,
    )
    response = _accepted_response(query_result=query_result)
    monkeypatch.setattr("src.cli.main._run_ask_pipeline", _fake_ask_pipeline(response))
    monkeypatch.setenv("FORGE_API_KEY", "test-key")

    class _ChartLlm:
        def __init__(self, config: object) -> None:
            del config

        def complete(self, prompt: str) -> str:
            del prompt
            return (
                '{"chart_type": "bar", "x_field": "region", "y_field": "total_sales", '
                '"title": "Total sales by region", "aggregation": "sum"}'
            )

    monkeypatch.setattr("src.ai_engineering.llm_client.LlmClient", _ChartLlm)
    main(["chart", "plot total sales by region"])
    captured = capsys.readouterr()
    assert "Chart saved to" in captured.out
    assert "Data points plotted: 2" in captured.out


def test_chart_command_requires_a_question(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["chart"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Usage" in captured.err
