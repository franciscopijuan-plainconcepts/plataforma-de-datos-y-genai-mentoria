"""Create/reexecute/delete only our temporary card, using the existing BI role."""

import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from dotenv import load_dotenv
import pytest

from src.ai_engineering.metabase_client import MetabaseClient
from src.contracts.metabase import MetabaseConfig, MetabaseSession
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import (
    GeneratedSql, NLQuestion, QueryResult, QueryRow, TextToSqlResponse, ValidationResult,
)
from src.data_access.adapters.postgres.connection import PostgresConfig, connect
from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver
from tests.semantic_fixtures import _build_test_table_defs


def test_actual_card_reexecutes_effective_sql_with_existing_role() -> None:
    if os.environ.get("RUN_SEMANTIC_METABASE_TESTS") != "1":
        pytest.skip("Set RUN_SEMANTIC_METABASE_TESTS=1 for a temporary real Metabase card")
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False)
    config = MetabaseConfig.from_env()
    assert urlparse(config.host).hostname in {"localhost", "127.0.0.1", "::1"}
    state = MetabaseSession.model_validate_json((root / ".artifacts/metabase_state.json").read_text())
    generated = 'SELECT "Region", CAST(SUM("Sales") AS TEXT) AS total FROM "Orders" GROUP BY "Region"'
    with connect(PostgresConfig.from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute('SELECT "Region" FROM "Orders" WHERE "Region" IS NOT NULL ORDER BY "Region" LIMIT 1')
            region_row = cur.fetchone()
            assert region_row is not None and isinstance(region_row["Region"], str)
            viewer = SemanticViewer(viewer_id="migration_reference", regions=[region_row["Region"]])
            effective = SemanticQueryResolver().apply_rls(generated, viewer, _build_test_table_defs()[0])
            cur.execute(effective)
            rows = cur.fetchall()
        conn.rollback()
    candidate = TextToSqlResponse(
        question=NLQuestion(text=f"Temporary Ossie migration verification {uuid4().hex}"),
        generated_sql=GeneratedSql(sql=generated, model_name="deterministic-reference", raw_response={}),
        validation=ValidationResult(accepted=True, sql=generated),
        query_result=QueryResult(sql=effective, rows=[QueryRow(data=row) for row in rows],
                                 row_count=len(rows), latency_ms=0),
    )
    client = MetabaseClient(config)
    card_id: int | None = None
    try:
        database = client._request_with_reauth("GET", f"/api/database/{state.metabase_db_id}")
        database.raise_for_status()
        assert database.json()["details"]["user"] == "metabase_readonly"
        card = client.send_governed_query(
            candidate, viewer, collection_id=state.collection_id, db_id=state.metabase_db_id,
        )
        assert card is not None, "Temporary card creation failed; no setup/role changes attempted"
        card_id = card.id
        stored = client._request_with_reauth("GET", f"/api/card/{card_id}")
        stored.raise_for_status()
        stored_query = stored.json()["dataset_query"]
        if stored_query.get("lib/type") == "mbql/query":
            assert len(stored_query["stages"]) == 1
            stage = stored_query["stages"][0]
            assert stage["lib/type"] == "mbql.stage/native"
            assert stage["native"] == effective
        else:
            assert stored_query["native"]["query"] == effective
        rerun = client._request_with_reauth(
            "POST", f"/api/card/{card_id}/query", json_payload={"ignore_cache": True},
        )
        rerun.raise_for_status()
        payload = rerun.json()
        assert payload["status"] == "completed"
        assert payload["data"]["rows"] == [list(row.values()) for row in rows]
    finally:
        try:
            if card_id is not None:
                assert client.delete_card(card_id), f"Clean up temporary Metabase card {card_id}"
        finally:
            client._client.close()
