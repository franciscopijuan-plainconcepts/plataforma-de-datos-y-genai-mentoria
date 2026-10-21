"""Validate the candidate against the workbook dictionary and actual PK constraints."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import pytest

from src.contracts.ingestion import SchemaInferenceResult
from src.data_access.adapters.postgres.connection import PostgresConfig, connect
from src.data_engineering.dictionary.generator import generate_dictionary
from src.data_engineering.eda.explorer import explore_workbook
from src.data_engineering.eda.schema_inferrer import infer_table_defs
from src.data_engineering.ingestion.manifest import sha256_of_file
from src.data_engineering.semantic_layer.ossie import canonicalize_ossie, export_ossie, import_ossie
from tests.semantic_fixtures import _load_legacy_semantic_layer


def test_candidate_matches_legacy_and_real_primary_keys() -> None:
    if os.environ.get("RUN_SEMANTIC_PG_TESTS") != "1":
        pytest.skip("Set RUN_SEMANTIC_PG_TESTS=1 for workbook and actual schema validation")
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False)
    workbook = root / "Global Superstore Data.xlsx"
    tables, shared = explore_workbook(workbook)
    source_sha = sha256_of_file(workbook)
    dictionary = generate_dictionary(
        SchemaInferenceResult(
            source_file=str(workbook), source_sha256=source_sha,
            tables=tables, shared_columns=shared, inferred_at=datetime.now(timezone.utc),
        ),
        infer_table_defs(tables),
    )
    legacy = _load_legacy_semantic_layer()
    text = (root / "semantic/sales.ossie.yaml").read_text()
    restored = import_ossie(text, dictionary)
    assert canonicalize_ossie(text) == export_ossie(legacy, dictionary)
    assert {metric.name: metric for metric in restored.metrics} == {metric.name: metric for metric in legacy.metrics}
    assert {dim.name: dim for dim in restored.dimensions} == {dim.name: dim for dim in legacy.dimensions}
    raw = json.loads(canonicalize_ossie(text))
    declared: dict[str, list[str]] = {}
    for dataset in raw["datasets"]:
        physical = {
            field["name"]: json.loads(field["custom_extensions"][0]["data"])["physical_column"]
            for field in dataset["fields"]
        }
        declared[dataset["name"]] = [physical[key] for key in dataset["primary_key"]]
    with connect(PostgresConfig.from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                "SELECT tc.table_name, array_agg(kcu.column_name::text ORDER BY kcu.ordinal_position) AS columns "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema "
                "AND tc.table_name=kcu.table_name "
                "WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public' "
                "AND tc.table_name IN ('Orders','Returns','People') GROUP BY tc.table_name"
            )
            actual = {row["table_name"]: row["columns"] for row in cur.fetchall()}
        conn.rollback()
    assert declared == actual
    assert "Order ID" not in declared["Orders"] and "Order ID" not in declared["Returns"]
    assert raw["relationships"] == []
