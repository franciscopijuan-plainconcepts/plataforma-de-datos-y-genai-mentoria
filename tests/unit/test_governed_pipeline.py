"""Effective SQL is request scoped and publication follows successful execution."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.ai_engineering.llm_client import LlmClient
from src.ai_engineering.pipeline import TextToSqlPipeline
from src.contracts.data_access import TableDef
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import (
    GeneratedSql, GovernedExecutionProvider, LlmConfig, NLQuestion, QueryRow,
)
from src.data_access.interfaces import QueryProvider
from src.data_engineering.semantic_layer.governed_provider import GovernedQueryProvider
from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver
from tests.semantic_fixtures import _build_test_dictionary, _build_test_table_defs


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.sql: list[str] = []
        self.fail = fail

    def execute_readonly_query(self, sql: str, table_def: TableDef) -> list[QueryRow]:
        self.sql.append(sql)
        if self.fail:
            raise RuntimeError("Warehouse execution failed")
        return [QueryRow(data={"total": 150})]


def make_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str, fail: bool = False,
) -> tuple[TextToSqlPipeline, Recorder, Mock, GovernedQueryProvider]:
    table = _build_test_table_defs()[0]
    viewer = SemanticViewer(viewer_id="north", regions=["North"])
    recorder = Recorder(fail)
    provider = GovernedQueryProvider(recorder, SemanticQueryResolver(), viewer, table, "model-sha")
    llm = Mock(spec=LlmClient)
    llm.generate_sql.return_value = GeneratedSql(sql=sql, model_name="test", raw_response={})
    sink = Mock()
    monkeypatch.setattr("src.ai_engineering.pipeline._LOG_PATH", tmp_path / "calls.log")
    pipeline = TextToSqlPipeline(
        dictionary=_build_test_dictionary(_build_test_table_defs()), table_def=table,
        llm_client=llm, query_provider=provider,
        llm_config=LlmConfig(api_key="unit-test-not-a-secret"), viewer=viewer,
        on_query_complete=sink,
    )
    return pipeline, recorder, sink, provider


def test_effective_sql_matches_executor_and_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    generated = 'SELECT SUM("Sales") AS total FROM "Orders"'
    pipeline, recorder, sink, provider = make_pipeline(tmp_path, monkeypatch, generated)
    response = pipeline.run(NLQuestion(text="total sales"))
    assert isinstance(provider, QueryProvider)
    assert isinstance(provider, GovernedExecutionProvider)
    assert response.query_result is not None and response.query_result.error is None
    assert response.generated_sql.sql == generated
    assert recorder.sql == [response.query_result.sql]
    assert response.query_result.sql != generated
    assert response.query_result.sql.count('"Region" IN') == 1
    assert response.query_result.model_sha256 == "model-sha"
    sink.assert_called_once_with(response, SemanticViewer(viewer_id="north", regions=["North"]))
    audit = (tmp_path / "calls.log").read_text()
    assert "effective_sql=" in audit and "model_sha256=model-sha" in audit


@pytest.mark.parametrize("sql,fail", [
    ('SELECT SUM("Sales") FROM "Orders"', True),
    ('DELETE FROM "Orders"', False),
    ('SELECT * FROM "People"', False),
])
def test_failed_or_rejected_queries_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str, fail: bool,
) -> None:
    pipeline, recorder, sink, _provider = make_pipeline(tmp_path, monkeypatch, sql, fail)
    response = pipeline.run(NLQuestion(text="test failure"))
    sink.assert_not_called()
    if fail:
        assert response.query_result is not None and response.query_result.error
        assert recorder.sql == [response.query_result.sql]
    else:
        assert not response.validation.accepted
        assert not recorder.sql


def test_sink_failure_does_not_hide_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline, recorder, sink, _provider = make_pipeline(
        tmp_path, monkeypatch, 'SELECT COUNT(*) FROM "Orders"',
    )
    sink.side_effect = RuntimeError("Metabase unavailable")
    response = pipeline.run(NLQuestion(text="count"))
    assert response.query_result is not None and response.query_result.error is None
    assert len(recorder.sql) == 1


def test_prepared_queries_have_no_mutable_last_sql(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pipeline, recorder, _sink, provider = make_pipeline(
        tmp_path, monkeypatch, 'SELECT COUNT(*) FROM "Orders"',
    )
    table = _build_test_table_defs()[0]
    first = provider.prepare_query('SELECT COUNT(*) FROM "Orders"', table)
    second = provider.prepare_query('SELECT SUM("Sales") FROM "Orders"', table)
    assert recorder.sql == []
    provider.execute_prepared_query(second, table)
    provider.execute_prepared_query(first, table)
    assert recorder.sql == [second.sql, first.sql]
    assert all(sql.count('"Region" IN') == 1 for sql in recorder.sql)


def test_prepared_viewer_cannot_be_substituted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pipeline, recorder, _sink, provider = make_pipeline(
        tmp_path, monkeypatch, 'SELECT COUNT(*) FROM "Orders"',
    )
    table = _build_test_table_defs()[0]
    prepared = provider.prepare_query('SELECT COUNT(*) FROM "Orders"', table)
    other = prepared.model_copy(update={"viewer": SemanticViewer(viewer_id="south", regions=["South"])})
    with pytest.raises(ValueError, match="context differs"):
        provider.execute_prepared_query(other, table)
    assert not recorder.sql
