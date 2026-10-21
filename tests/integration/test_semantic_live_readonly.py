"""Optional read-only comparison on the user's existing warehouse; no LLM or BI writes."""

from decimal import Decimal
import os
from pathlib import Path
import json
from unittest.mock import Mock

from dotenv import load_dotenv
import pytest
import sqlglot

from src.contracts.semantic_layer import SemanticViewer
from src.contracts.data_access import TableDef
from src.contracts.text_to_sql import GeneratedSql, QueryRow
from src.data_access.adapters.postgres.connection import PostgresConfig, connect
from src.data_access.adapters.postgres.repository import PostgresRepository
from src.data_engineering.semantic_layer.resolver import SemanticQueryResolver
from src.data_engineering.semantic_layer.ossie import canonicalize_ossie, conversion_report
from src.mlops.dataset import extract_feature_set
from tests.semantic_fixtures import _build_test_table_defs, _load_legacy_semantic_layer, _load_test_semantic_layer


def test_live_regional_metric_preservation() -> None:
    if os.environ.get("RUN_SEMANTIC_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_SEMANTIC_LIVE_TESTS=1 to compare existing warehouse read-only")
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    metrics = _load_test_semantic_layer().metrics
    legacy = {metric.name: metric for metric in _load_legacy_semantic_layer().metrics}
    assert set(legacy) == {metric.name for metric in metrics}
    with connect(PostgresConfig.from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute('SELECT DISTINCT "Region" FROM "Orders" WHERE "Region" IS NOT NULL')
            regions = [row["Region"] for row in cur.fetchall()]
            for region in regions:
                assert isinstance(region, str)
                viewer = SemanticViewer(viewer_id="reference", regions=[region])
                for metric in metrics:
                    generated = sqlglot.parse_one(
                        f'SELECT {metric.formula_sql} AS value FROM "Orders"', read="postgres",
                    ).sql(dialect="postgres", identify=True)
                    reference = sqlglot.parse_one(
                        f'SELECT {legacy[metric.name].formula_sql} AS value FROM "Orders"', read="postgres",
                    ).sql(dialect="postgres", identify=True)
                    cur.execute(reference + ' WHERE "Region" = %s', (region,))
                    expected = cur.fetchone()
                    effective = SemanticQueryResolver().apply_rls(
                        generated, viewer, _build_test_table_defs()[0],
                    )
                    cur.execute(effective)
                    actual = cur.fetchone()
                    assert expected is not None and actual is not None
                    assert type(actual["value"]) is type(expected["value"])
                    if isinstance(actual["value"], Decimal) and metric.name in {
                        "return_rate", "avg_order_value", "net_profit",
                    }:
                        assert isinstance(expected["value"], Decimal)
                        assert abs(actual["value"] - expected["value"]) <= Decimal("1e-12")
                    else:
                        assert actual == expected, f"{metric.name} differs for region {region}"
        conn.rollback()


def test_live_mlops_extraction_keeps_raw_query_provider() -> None:
    if os.environ.get("RUN_SEMANTIC_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_SEMANTIC_LIVE_TESTS=1 for read-only existing MLOps extraction")
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    with connect(PostgresConfig.from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute('SELECT COUNT(*) AS count FROM "Orders"')
            expected = cur.fetchone()
        assert expected is not None
        features = extract_feature_set(PostgresRepository(conn=conn), _build_test_table_defs()[0])
        assert features.row_count == expected["count"]
        assert len(features.rows) == features.row_count
        assert len(features.data_hash) == 64
        assert all(isinstance(row.sales, Decimal) for row in features.rows)
        conn.rollback()


def test_canonical_hash_reaches_real_execution_without_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    if os.environ.get("RUN_SEMANTIC_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_SEMANTIC_LIVE_TESTS=1 for canonical CLI execution")
    from src.ai_engineering.llm_client import LlmClient
    from src.cli import main as cli

    original = cli._OSSIE_MODEL_PATH.read_text()
    raw = json.loads(canonicalize_ossie(original))
    metric = next(item for item in raw["metrics"] if item["name"] == "gross_sales")
    metric["expression"]["dialects"][0]["expression"] = 'SUM("Profit")'
    changed = tmp_path / "changed.ossie.json"
    changed.write_text(json.dumps(raw))
    expected_hash = conversion_report(changed.read_text()).model_sha256
    assert expected_hash != conversion_report(original).model_sha256
    monkeypatch.setattr(cli, "_OSSIE_MODEL_PATH", changed)
    monkeypatch.setenv("FORGE_API_KEY", "deterministic-no-network")
    monkeypatch.setattr("src.ai_engineering.pipeline._LOG_PATH", tmp_path / "audit.log")
    llm = Mock(spec=LlmClient)
    llm.generate_sql.return_value = GeneratedSql(
        sql='SELECT SUM("Profit") AS total FROM "Orders"', model_name="deterministic", raw_response={},
    )
    monkeypatch.setattr("src.ai_engineering.llm_client.LlmClient", Mock(return_value=llm))
    captured: list[str] = []
    execute = PostgresRepository.execute_readonly_query

    def record(self: PostgresRepository, sql: str, table_def: TableDef) -> list[QueryRow]:
        captured.append(sql)
        return execute(self, sql, table_def)

    monkeypatch.setattr(PostgresRepository, "execute_readonly_query", record)
    response, viewer = cli._run_ask_pipeline(
        "deterministic reference", "marilene_rousseau", False, metabase_enabled=False,
    )
    assert viewer.regions == ["Caribbean"]
    assert response.query_result is not None and response.query_result.error is None
    assert response.query_result.sql == captured[-1]
    assert response.query_result.model_sha256 == expected_hash
    assert response.query_result.provenance is not None
    assert response.query_result.provenance.ossie_revision == conversion_report(changed.read_text()).ossie_revision
    assert isinstance(response.query_result.rows[0].data["total"], Decimal)
    assert expected_hash in (tmp_path / "audit.log").read_text()
