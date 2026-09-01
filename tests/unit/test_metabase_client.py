"""Unit tests for the MetabaseClient (no Docker Metabase required).

Uses `httpx.MockTransport` to fake API responses. Covers:
- login + token caching
- re-auth on 401
- is_setup_complete (true/false branches)
- create_card payload shape
- get_or_create_collection idempotency
- send_governed_query best-effort (returns None on HTTP error; never raises)
- _infer_display_type heuristics

Reference: specs/004-metabase-integration/contracts/metabase_client.md
            specs/004-metabase-integration/tasks.md T010, T021, T022
            specs/004-metabase-integration/research.md Parts E, F, G
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import httpx
import pytest

from src.ai_engineering.metabase_client import MetabaseClient
from src.contracts.metabase import Card, Collection, Dashboard, MetabaseConfig
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import (
    GeneratedSql,
    NLQuestion,
    QueryResult,
    TextToSqlResponse,
    ValidationResult,
)


def _make_config() -> MetabaseConfig:
    return MetabaseConfig(
        host="http://test.local",
        admin_email="admin@test.local",
        admin_password="test-pass",
        port=3000,
    )


def _client_with_handler(handler: Callable[[httpx.Request], httpx.Response]) -> MetabaseClient:
    """Build a MetabaseClient whose underlying httpx.Client uses a MockTransport."""
    config = _make_config()
    client = MetabaseClient.__new__(MetabaseClient)
    client._config = config
    transport = httpx.MockTransport(handler)
    client._client = httpx.Client(
        base_url=config.host, transport=transport, timeout=5.0
    )
    client._session_token = None
    return client


# --- Login + token caching -----------------------------------------------


def test_login_caches_session_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/session"
        return httpx.Response(200, json={"id": "test-token-123"})

    client = _client_with_handler(handler)
    token1 = client.login()
    token2 = client.login()
    assert token1 == token2 == "test-token-123"


def test_login_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    client = _client_with_handler(handler)
    with pytest.raises(httpx.HTTPError):
        client.login()


# --- Re-auth on 401 -----------------------------------------------------


def test_reauth_on_401_retries_once() -> None:
    """If the first authenticated call returns 401, the client re-logins and retries ONCE."""
    state = {"auth_calls": 0, "data_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            state["auth_calls"] += 1
            return httpx.Response(200, json={"id": "fresh-token"})
        if request.url.path == "/api/collection":
            state["data_calls"] += 1
            if state["data_calls"] == 1:
                return httpx.Response(401, json={"error": "expired token"})
            return httpx.Response(200, json=[{"id": 7, "name": "x", "location": "/"}])
        return httpx.Response(404)

    client = _client_with_handler(handler)
    # Prime the session token; then the first /api/collection call returns 401 →
    # the _request_with_reauth() retry kicks in: re-login, retry, get 200.
    client._session_token = "stale-token"
    colls = client.get_or_create_collection("x")
    assert colls is not None
    assert colls.id == 7
    assert state["data_calls"] == 2  # retry happened
    assert state["auth_calls"] == 1  # one re-login


# --- is_setup_complete --------------------------------------------------


def test_is_setup_complete_true_when_setup_token_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"setup-token": None})

    client = _client_with_handler(handler)
    assert client.is_setup_complete() is True


def test_is_setup_complete_false_when_setup_token_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"setup-token": "abc-token-123"})

    client = _client_with_handler(handler)
    assert client.is_setup_complete() is False


# --- get_or_create_collection idempotency -------------------------------


def test_get_or_create_collection_returns_existing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": 7, "name": "Chat Sessions", "parent_id": None, "location": "/"},
                ],
            )
        # If anything reaches POST, the test should fail loudly.
        return httpx.Response(400, json={"error": "should not POST"})

    client = _client_with_handler(handler)
    client._session_token = "test-token"
    coll = client.get_or_create_collection("Chat Sessions")
    assert coll is not None
    assert coll.id == 7
    assert coll.name == "Chat Sessions"


def test_get_or_create_collection_creates_when_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"id": 9, "name": "Chat Sessions", "parent_id": None, "location": "/"},
            )
        return httpx.Response(400)

    client = _client_with_handler(handler)
    client._session_token = "test-token"
    coll = client.get_or_create_collection("Chat Sessions")
    assert coll is not None
    assert coll.id == 9


# --- create_card --------------------------------------------------------


def test_create_card_returns_card_with_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url.path)
        import json as _json

        captured["body"] = _json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"id": 42, "name": "Total Sales", "display": "bar"},
        )

    client = _client_with_handler(handler)
    client._session_token = "test-token"
    card = client.create_card(
        name="Total Sales",
        sql='SELECT SUM("Sales") FROM Orders',
        collection_id=7,
        display="bar",
        description="viewer_id=alice",
    )
    assert card is not None
    assert card.id == 42
    assert card.sql == 'SELECT SUM("Sales") FROM Orders'
    assert card.display == "bar"
    assert card.description == "viewer_id=alice"
    # The POST body must contain the native SQL.
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["dataset_query"]["native"]["query"] == 'SELECT SUM("Sales") FROM Orders'
    assert body["display"] == "bar"


def test_create_card_returns_none_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client_with_handler(handler)
    client._session_token = "test-token"
    card = client.create_card(
        name="X", sql="SELECT 1", collection_id=1, display="table"
    )
    assert card is None


# --- send_governed_query best-effort (T022) ----------------------------


def _build_response(rows: list[dict[str, object]], sql: str) -> TextToSqlResponse:
    from src.contracts.text_to_sql import QueryRow

    query_rows = [QueryRow(data=r) for r in rows]
    return TextToSqlResponse(
        question=NLQuestion(text="total sales"),
        generated_sql=GeneratedSql(sql=sql, model_name="test", raw_response={}),
        validation=ValidationResult(accepted=True, reason=None, sql=sql),
        query_result=QueryResult(
            sql=sql,
            rows=query_rows,
            row_count=len(query_rows),
            latency_ms=5,
            error=None,
        ),
    )


def test_send_governed_query_returns_none_on_no_collection_id() -> None:
    client = _client_with_handler(lambda r: httpx.Response(200, json={}))
    viewer = SemanticViewer(viewer_id="alice", regions=["Caribbean"])
    response = _build_response([{"total": Decimal("10.0")}], 'SELECT SUM("Sales") FROM Orders')
    card = client.send_governed_query(response, viewer, collection_id=None)
    assert card is None


def test_send_governed_query_never_raises_on_http_error() -> None:
    """Best-effort (FR-013): when the API errors, the method returns None and never raises."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with_handler(handler)
    client._session_token = "test-token"
    viewer = SemanticViewer(viewer_id="alice", regions=["Caribbean"])
    response = _build_response([{"total": Decimal("10.0")}], 'SELECT SUM("Sales") FROM Orders')
    card = client.send_governed_query(response, viewer, collection_id=7)
    assert card is None


def test_send_governed_query_creates_card_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/card":
            return httpx.Response(200, json={"id": 100, "name": "total sales", "display": "scalar"})
        return httpx.Response(200, json={})

    client = _client_with_handler(handler)
    client._session_token = "test-token"
    viewer = SemanticViewer(viewer_id="alice", regions=["Caribbean"])
    response = _build_response([{"total": Decimal("10.0")}], 'SELECT SUM("Sales") FROM Orders')
    card = client.send_governed_query(response, viewer, collection_id=7)
    assert card is not None
    assert card.id == 100
    assert card.display == "scalar"  # single-row single-col
    # The card description must record the viewer_id for governance traceability.
    assert card.description is not None
    assert "viewer_id=alice" in card.description
    assert "gov_bypass=False" in card.description


# --- _infer_display_type heuristics (T021) -----------------------------


def test_display_scalar_for_single_value() -> None:
    response = _build_response([{"total": Decimal("10.0")}], 'SELECT SUM("Sales") FROM Orders')
    assert MetabaseClient._infer_display_type(response) == "scalar"


def test_display_bar_for_group_by_with_few_rows() -> None:
    from typing import cast

    rows = cast(list[dict[str, object]], [{"region": "Caribbean"}, {"region": "Central US"}])
    response = _build_response(
        rows,
        'SELECT "Region" FROM Orders GROUP BY "Region"',
    )
    assert MetabaseClient._infer_display_type(response) == "bar"


def test_display_table_when_more_than_20_rows() -> None:
    """Even with GROUP BY, more than 20 rows falls back to table display."""
    from typing import cast

    rows = cast(list[dict[str, object]], [{"region": f"R{i}"} for i in range(25)])
    response = _build_response(rows, 'SELECT "Region" FROM Orders GROUP BY "Region"')
    assert MetabaseClient._infer_display_type(response) == "table"


def test_display_table_default_when_no_group_by() -> None:
    response = _build_response([{"x": 1}, {"y": 2}], "SELECT * FROM Orders")
    assert MetabaseClient._infer_display_type(response) == "table"
