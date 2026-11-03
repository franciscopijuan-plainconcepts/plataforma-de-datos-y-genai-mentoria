"""CLI messages for an unknown viewer and for answers with no data in scope."""

from unittest.mock import MagicMock, Mock

import pytest

from src.cli import main as cli
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import (
    GeneratedSql, NLQuestion, QueryResult, QueryRow, TextToSqlResponse, ValidationResult,
)
from src.data_engineering.semantic_layer import person_resolver, registry


def _response(rows: list[QueryRow]) -> TextToSqlResponse:
    return TextToSqlResponse(
        question=NLQuestion(text="next month forecast"),
        generated_sql=GeneratedSql(sql='SELECT AVG("Predicted Sales") FROM "Predictions"', model_name="t", raw_response={}),
        validation=ValidationResult(accepted=True, sql="generated"),
        query_result=QueryResult(sql="governed", rows=rows, row_count=len(rows), latency_ms=1),
    )


@pytest.mark.parametrize("rows", [[], [QueryRow(data={})]])
def test_ask_explains_empty_results(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rows: list[QueryRow],
) -> None:
    viewer = SemanticViewer(viewer_id="marilene_rousseau", regions=["Caribbean"])
    monkeypatch.setattr(cli, "_run_ask_pipeline", Mock(return_value=(_response(rows), viewer)))
    cli.main(["ask", "next month forecast", "--viewer", "marilene_rousseau", "--no-metabase"])
    out = capsys.readouterr().out
    assert "(no data for this question in the viewer's scope)" in out
    assert "{}" not in out


def test_ask_prints_rows_with_data(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    viewer = SemanticViewer(viewer_id="vasco_magalhaes", regions=["South America"])
    rows = [QueryRow(data={"avg": 88.33})]
    monkeypatch.setattr(cli, "_run_ask_pipeline", Mock(return_value=(_response(rows), viewer)))
    cli.main(["ask", "next month forecast", "--viewer", "vasco_magalhaes", "--no-metabase"])
    out = capsys.readouterr().out
    assert "{'avg': 88.33}" in out and "no data" not in out


@pytest.mark.parametrize("registry_error, hint", [
    (FileNotFoundError("Viewers config not found at viewers.yaml"), "No viewers.yaml exists"),
    (ValueError("Viewer 'nobody' not found"), "Configure custom viewers in viewers.yaml"),
])
def test_unknown_viewer_lists_people(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    registry_error: Exception, hint: str,
) -> None:
    resolver = MagicMock()
    resolver.resolve.return_value = None
    resolver.list_available.return_value = ["gilbert_wolff", "vasco_magalhaes"]
    monkeypatch.setattr(person_resolver, "PeopleViewerResolver", Mock(return_value=resolver))
    monkeypatch.setattr(registry.ViewerRegistry, "get_viewer", Mock(side_effect=registry_error))
    monkeypatch.setattr(cli, "PostgresRepository", MagicMock())
    with pytest.raises(SystemExit):
        cli._resolve_viewer("nobody", False, MagicMock())
    err = capsys.readouterr().err
    assert "Viewer 'nobody' not found in People" in err
    assert "['gilbert_wolff', 'vasco_magalhaes']" in err and hint in err
    assert "Viewers config not found" not in err
