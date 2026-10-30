"""v3.3 predictive dashboard: cards fed from Ossie metrics, governed SQL, Metabase layout."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import httpx
import pytest
from sqlglot import exp
import sqlglot

from src.ai_engineering import metabase_client
from src.cli import main as cli
from src.contracts.metabase import Card, Dashboard, DashcardPlacement, MetabaseConfig, MetabaseSession
from src.contracts.text_to_sql import PreparedQuery
from src.data_engineering.semantic_layer import governed_provider
from src.mlops import registry as mlops_registry
from src.contracts.semantic_layer import SemanticViewer
from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver
from src.mlops.predictions_store import forecast_accuracy_view_def, predictions_table_def
from src.reporting.forecast_dashboard import ForecastCardSpec, build_forecast_cards
from tests.semantic_fixtures import _build_test_table_defs, _load_test_semantic_layer
from tests.unit.test_metabase_client import _client_with_handler


_EXTRA = {"predictions": predictions_table_def(), "forecast accuracy": forecast_accuracy_view_def()}


def _cards() -> list[ForecastCardSpec]:
    return build_forecast_cards(_load_test_semantic_layer(), run_id="run-1", environment="prod")


def test_cards_use_the_ossie_metric_formulas() -> None:
    layer = _load_test_semantic_layer()
    formulas = {metric.name: metric.formula_sql for metric in layer.metrics}
    cards = {card.key: card for card in _cards()}
    assert set(cards) == {"next_month", "wape", "bias", "history", "forecast_months", "backtest"}
    assert formulas["forecast_wape"] in cards["wape"].sql
    assert formulas["forecast_bias"] in cards["bias"].sql
    assert formulas["gross_sales"] in cards["history"].sql
    assert formulas["avg_predicted_line_sales"] in cards["next_month"].sql
    assert formulas["backtest_actual_sales"] in cards["backtest"].sql
    assert "\"Run ID\" = 'run-1'" in cards["backtest"].sql
    assert "\"Environment\" = 'prod'" in cards["forecast_months"].sql


def test_missing_or_misplaced_metric_fails_loudly() -> None:
    layer = _load_test_semantic_layer()
    without = layer.model_copy(update={"metrics": [m for m in layer.metrics if m.name != "forecast_wape"]})
    with pytest.raises(ValueError, match="forecast_wape"):
        build_forecast_cards(without, run_id="r", environment="prod")
    misplaced = layer.model_copy(update={"metrics": [
        m.model_copy(update={"source_table": "Predictions"}) if m.name == "gross_sales" else m
        for m in layer.metrics
    ]})
    with pytest.raises(ValueError, match="gross_sales"):
        build_forecast_cards(misplaced, run_id="r", environment="prod")


def test_every_card_is_governed_by_region() -> None:
    viewer = SemanticViewer(viewer_id="caribbean", regions=["Caribbean"])
    orders = _build_test_table_defs()[0]
    for card in _cards():
        governed = SemanticQueryResolver().apply_rls(card.sql, viewer, orders, extra_tables=_EXTRA)
        scans = [table for table in sqlglot.parse_one(governed, read="postgres").find_all(exp.Table)]
        assert scans, card.key
        for table in scans:
            subquery = table.parent_select
            assert subquery is not None and "\"Region\" IN ('Caribbean')" in subquery.sql(dialect="postgres"), card.key


def test_layout_fits_the_grid_without_overlap() -> None:
    occupied: set[tuple[int, int]] = set()
    for card in _cards():
        assert card.col + card.size_x <= 24
        cells = {(card.row + r, card.col + c) for r in range(card.size_y) for c in range(card.size_x)}
        assert not cells & occupied, card.key
        occupied |= cells


def test_set_dashboard_cards_puts_full_layout() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"id": "token"})
        seen.append(request)
        return httpx.Response(200, json={"id": 7})

    client = _client_with_handler(handler)
    placements = [DashcardPlacement(card_id=11, row=0, col=0, size_x=8, size_y=4),
                  DashcardPlacement(card_id=12, row=4, col=0, size_x=24, size_y=8)]
    assert client.set_dashboard_cards(7, placements) is True
    assert seen[0].method == "PUT" and seen[0].url.path == "/api/dashboard/7"
    body = json.loads(seen[0].content)
    assert [(item["id"], item["card_id"], item["size_x"]) for item in body["dashcards"]] == [(-1, 11, 8), (-2, 12, 24)]


def test_dashboard_card_ids_and_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"id": "token"})
        if request.url.path == "/api/dashboard/7":
            return httpx.Response(200, json={"dashcards": [{"card_id": 3}, {"card_id": None}, {"card_id": 4}]})
        return httpx.Response(500)

    client = _client_with_handler(handler)
    assert client.get_dashboard_card_ids(7) == [3, 4]
    assert client.get_dashboard_card_ids(8) is None
    assert client.set_dashboard_cards(8, []) is False


# --- CLI composition: governed execution before publishing, idempotency ---

class _FakeProvider:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on

    def prepare_query(self, sql: str, table_def: object) -> PreparedQuery:
        return PreparedQuery(generated_sql=sql, sql=f"/*governed*/ {sql}",
                             viewer=_VIEWER, model_sha256="sha")

    def execute_prepared_query(self, query: PreparedQuery, table_def: object) -> list[object]:
        if self.fail_on and self.fail_on in query.sql:
            raise RuntimeError("boom")
        return []


class _FakeMetabase:
    def __init__(self, existing: list[int]) -> None:
        self.existing = existing
        self.calls: list[tuple[str, object]] = []

    def get_or_create_dashboard(self, name: str, collection_id: int) -> Dashboard:
        return Dashboard(id=9, name=name, collection_id=collection_id)

    def get_dashboard_card_ids(self, dashboard_id: int) -> list[int]:
        return self.existing

    def create_card(self, **kwargs: object) -> Card:
        self.calls.append(("create", kwargs["sql"]))
        return Card(id=100 + len(self.calls), name=str(kwargs["name"]), sql=str(kwargs["sql"]),
                    collection_id=5, display="table")

    def set_dashboard_cards(self, dashboard_id: int, placements: list[DashcardPlacement]) -> bool:
        self.calls.append(("layout", len(placements)))
        return True

    def delete_card(self, card_id: int) -> bool:
        self.calls.append(("delete", card_id))
        return True


_VIEWER = SemanticViewer(viewer_id="marilene_rousseau", regions=["Caribbean"])


def _wire(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider, metabase: _FakeMetabase) -> None:
    layer = _load_test_semantic_layer()
    monkeypatch.setattr(cli, "_load_semantic_context", lambda: (None, _build_test_table_defs()[0], layer, ""))
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    monkeypatch.setattr(cli, "_load_metabase_state", lambda: MetabaseSession(
        configured_at=datetime.now(timezone.utc), admin_email="a@b.c", metabase_db_id=2, collection_id=5))
    monkeypatch.setattr(cli, "_resolve_viewer", lambda *args: _VIEWER)
    monkeypatch.setattr(cli, "PostgresRepository", MagicMock())
    monkeypatch.setattr(governed_provider, "build_governed_provider", lambda **kwargs: provider)
    registry = MagicMock()
    registry.return_value.resolve_active_run.return_value = MagicMock(run_id="run-1")
    monkeypatch.setattr(mlops_registry, "ArtifactRegistry", registry)
    monkeypatch.setattr(metabase_client, "MetabaseClient", lambda config: metabase)
    monkeypatch.setattr(MetabaseConfig, "from_env", classmethod(lambda cls: MetabaseConfig(
        host="http://mb.local", admin_email="a@b.c", admin_password="x")))


def test_cli_publishes_governed_cards_and_layout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    metabase = _FakeMetabase(existing=[])
    _wire(monkeypatch, _FakeProvider(), metabase)
    cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau"])
    created = [sql for kind, sql in metabase.calls if kind == "create"]
    assert len(created) == 6 and all(str(sql).startswith("/*governed*/") for sql in created)
    assert metabase.calls[-1] == ("layout", 6)
    assert "http://mb.local/dashboard/9" in capsys.readouterr().out


def test_cli_publishes_nothing_if_a_card_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    metabase = _FakeMetabase(existing=[1])
    _wire(monkeypatch, _FakeProvider(fail_on="forecast_bias"), metabase)
    with pytest.raises(SystemExit):
        cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau", "--replace"])
    assert metabase.calls == []


def test_cli_skips_existing_dashboard_unless_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    metabase = _FakeMetabase(existing=[1, 2])
    _wire(monkeypatch, _FakeProvider(), metabase)
    cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau"])
    assert metabase.calls == []
    cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau", "--replace"])
    kinds = [kind for kind, _ in metabase.calls]
    assert kinds == ["create"] * 6 + ["layout", "delete", "delete"]
