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
from src.contracts.metabase import (
    Card,
    Dashboard,
    DashboardCardRef,
    DashcardPlacement,
    MetabaseConfig,
    MetabaseSession,
)
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


def test_dashboard_cards_and_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"id": "token"})
        if request.url.path == "/api/dashboard/7":
            return httpx.Response(200, json={"dashcards": [
                {"id": 30, "card_id": 3, "row": 0, "col": 0, "size_x": 8, "size_y": 4,
                 "card": {"description": "source=forecast-dashboard run_id=r"}},
                {"id": 31, "card_id": None, "row": 4, "col": 0, "size_x": 24, "size_y": 2,
                 "visualization_settings": {"text": "nota"}, "card": {}},
            ]})
        if request.url.path == "/api/dashboard/9":
            return httpx.Response(200, json={"dashcards": [{"card_id": 3}]})
        return httpx.Response(500)

    client = _client_with_handler(handler)
    refs = client.get_dashboard_cards(7)
    assert refs is not None
    assert [(ref.placement.dashcard_id, ref.placement.card_id) for ref in refs] == [(30, 3), (31, None)]
    assert refs[0].description == "source=forecast-dashboard run_id=r" and refs[1].description is None
    assert refs[1].placement.visualization_settings == {"text": "nota"}
    assert client.get_dashboard_cards(8) is None
    assert client.get_dashboard_cards(9) is None  # dashcard without id: unexpected payload
    assert client.set_dashboard_cards(8, []) is False


def test_set_dashboard_cards_keeps_existing_dashcards() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"id": "token"})
        seen.append(request)
        return httpx.Response(200, json={"id": 7})

    client = _client_with_handler(handler)
    kept = DashcardPlacement(card_id=None, row=20, col=0, size_x=24, size_y=2, dashcard_id=31,
                             visualization_settings={"text": "nota"})
    assert client.set_dashboard_cards(7, [DashcardPlacement(card_id=11, row=0, col=0, size_x=8, size_y=4), kept])
    body = json.loads(seen[0].content)["dashcards"]
    assert [(item["id"], item["card_id"]) for item in body] == [(-1, 11), (31, None)]
    assert body[1]["visualization_settings"] == {"text": "nota"} and body[1]["row"] == 20


# --- CLI composition: governed execution before publishing, idempotency ---

class _FakeProvider:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.prepared = 0

    def prepare_query(self, sql: str, table_def: object) -> PreparedQuery:
        self.prepared += 1
        return PreparedQuery(generated_sql=sql, sql=f"/*governed*/ {sql}",
                             viewer=_VIEWER, model_sha256="sha")

    def execute_prepared_query(self, query: PreparedQuery, table_def: object) -> list[object]:
        if self.fail_on and self.fail_on in query.sql:
            raise RuntimeError("boom")
        return []


class _FakeMetabase:
    def __init__(self, existing: list[DashboardCardRef], reject_card: int | None = None,
                 layout_ok: bool = True) -> None:
        self.existing = existing
        self.reject_card = reject_card
        self.layout_ok = layout_ok
        self.calls: list[tuple[str, object]] = []
        self.descriptions: list[str] = []
        self.layout: list[DashcardPlacement] = []

    def get_or_create_dashboard(self, name: str, collection_id: int) -> Dashboard:
        return Dashboard(id=9, name=name, collection_id=collection_id)

    def get_dashboard_cards(self, dashboard_id: int) -> list[DashboardCardRef]:
        return self.existing

    def create_card(self, **kwargs: object) -> Card | None:
        if len(self.descriptions) + 1 == self.reject_card:
            return None
        self.calls.append(("create", kwargs["sql"]))
        self.descriptions.append(str(kwargs["description"]))
        return Card(id=100 + len(self.descriptions), name=str(kwargs["name"]), sql=str(kwargs["sql"]),
                    collection_id=5, display="table")

    def set_dashboard_cards(self, dashboard_id: int, placements: list[DashcardPlacement]) -> bool:
        self.calls.append(("layout", len(placements)))
        self.layout = placements
        return self.layout_ok

    def delete_card(self, card_id: int) -> bool:
        self.calls.append(("delete", card_id))
        return True


def _ref(dashcard_id: int, card_id: int | None, description: str | None, row: int = 0) -> DashboardCardRef:
    placement = DashcardPlacement(card_id=card_id, row=row, col=0, size_x=12, size_y=4, dashcard_id=dashcard_id)
    return DashboardCardRef(placement=placement, description=description)


_OWNED = "viewer_id=marilene_rousseau gov_bypass=False run_id=old source=forecast-dashboard created_at=x"


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
    assert all("gov_bypass=False" in item and "source=forecast-dashboard" in item.split()
               for item in metabase.descriptions)
    assert "http://mb.local/dashboard/9" in capsys.readouterr().out


def test_cli_description_reports_governance_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    metabase = _FakeMetabase(existing=[])
    _wire(monkeypatch, _FakeProvider(), metabase)
    bypass = SemanticViewer(viewer_id="dev", regions=["Caribbean"], allows_full_access=True, is_local_dev=True)
    monkeypatch.setattr(cli, "_resolve_viewer", lambda *args: bypass)
    cli.main(["metabase", "forecast-dashboard", "--viewer", "dev"])
    assert len(metabase.descriptions) == 6
    assert all("gov_bypass=True" in item for item in metabase.descriptions)


def test_cli_publishes_nothing_if_a_card_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    metabase = _FakeMetabase(existing=[_ref(1, 1, _OWNED)])
    _wire(monkeypatch, _FakeProvider(fail_on="forecast_bias"), metabase)
    with pytest.raises(SystemExit):
        cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau", "--replace"])
    assert metabase.calls == []


def test_cli_skips_existing_dashboard_before_running_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    metabase = _FakeMetabase(existing=[_ref(1, 1, _OWNED), _ref(2, 2, _OWNED)])
    provider = _FakeProvider()
    _wire(monkeypatch, provider, metabase)
    cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau"])
    assert metabase.calls == [] and provider.prepared == 0
    cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau", "--replace"])
    assert provider.prepared == 6
    kinds = [kind for kind, _ in metabase.calls]
    assert kinds == ["create"] * 6 + ["layout", "delete", "delete"]


def test_cli_replace_deletes_only_its_own_cards(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    foreign_card = _ref(11, 77, "a card a user added", row=0)
    foreign_text = _ref(12, None, None, row=4)
    metabase = _FakeMetabase(existing=[_ref(10, 5, _OWNED), foreign_card, foreign_text])
    _wire(monkeypatch, _FakeProvider(), metabase)
    cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau", "--replace"])
    assert [value for kind, value in metabase.calls if kind == "delete"] == [5]
    kept = [item for item in metabase.layout if item.dashcard_id is not None]
    assert [(item.dashcard_id, item.card_id) for item in kept] == [(11, 77), (12, None)]
    # Re-placed below the six new cards (rows 0-19), keeping their relative order.
    assert [item.row for item in kept] == [20, 24]
    assert "kept 2 other cards" in capsys.readouterr().out


@pytest.mark.parametrize("reject_card, layout_ok, rolled_back", [
    (3, True, [101, 102]),
    (None, False, [101, 102, 103, 104, 105, 106]),
])
def test_cli_rolls_back_created_cards_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    reject_card: int | None, layout_ok: bool, rolled_back: list[int],
) -> None:
    metabase = _FakeMetabase(existing=[_ref(1, 1, _OWNED)], reject_card=reject_card, layout_ok=layout_ok)
    _wire(monkeypatch, _FakeProvider(), metabase)
    with pytest.raises(SystemExit):
        cli.main(["metabase", "forecast-dashboard", "--viewer", "marilene_rousseau", "--replace"])
    # Only the cards of this run are removed; the dashboard's own cards stay.
    assert [value for kind, value in metabase.calls if kind == "delete"] == rolled_back
    assert f"removed the {len(rolled_back)} cards created by this run" in capsys.readouterr().err


def test_get_or_create_dashboard_lists_collection_items() -> None:
    """v0.58: find by name through the collection items, create only when missing."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"id": "token"})
        seen.append((request.method, str(request.url)))
        if request.url.path == "/api/collection/5/items":
            return httpx.Response(200, json={"data": [{"id": 3, "name": "Forecast: ana", "model": "dashboard"}]})
        return httpx.Response(200, json={"id": 4, "name": "Forecast: bob"})

    client = _client_with_handler(handler)
    found = client.get_or_create_dashboard("Forecast: ana", 5)
    assert found is not None and found.id == 3
    assert seen == [("GET", "http://test.local/api/collection/5/items?models=dashboard")]
    created = client.get_or_create_dashboard("Forecast: bob", 5)
    assert created is not None and created.id == 4 and seen[-1][0] == "POST"
