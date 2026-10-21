"""Exercise a pinned legacy code/model pair in a temporary checkout, never the worktree."""

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
import sqlglot

from src.contracts.semantic_layer import SemanticLayerDocument
from src.data_access.adapters.postgres.connection import DictConnection
from src.cli import main as cli
from tests.integration.test_semantic_reference import reference_connection
from tests.semantic_fixtures import LEGACY_REVISION


_READ_LEGACY = """
import json
from pathlib import Path
import src
from src.contracts.semantic_layer import SemanticLayerDocument
from src.data_engineering.semantic_layer.metrics import get_metrics
from src.data_engineering.semantic_layer.render import render_json
assert Path(src.__file__).resolve().is_relative_to(Path.cwd())
raw = json.loads(Path(".artifacts/semantic_layer.json").read_text())
raw["generated_at"] = "2026-01-01T00:00:00Z"
model = SemanticLayerDocument.model_validate(raw)
assert {m.name: m for m in model.metrics} == {m.name: m for m in get_metrics()}
print(render_json(model))
"""


def test_isolated_code_model_rollback_and_return(
    reference_connection: DictConnection, tmp_path: Path,
) -> None:
    if os.environ.get("RUN_SEMANTIC_PG_TESTS") != "1":
        pytest.skip("Set RUN_SEMANTIC_PG_TESTS=1 for isolated rollback validation")
    root = Path(__file__).resolve().parents[2]
    _dictionary, _table, active, active_text = cli._load_semantic_context()
    assert active.provenance is not None
    active_hash = active.provenance.model_sha256
    snapshot = tmp_path / "legacy"
    snapshot.mkdir()
    archived = subprocess.run(
        ["git", "archive", "--format=tar", LEGACY_REVISION, "src", ".artifacts/semantic_layer.json"],
        cwd=root, capture_output=True, check=True,
    )
    with tarfile.open(fileobj=BytesIO(archived.stdout)) as archive:
        archive.extractall(snapshot, filter="data")
    environment = {"PATH": os.environ["PATH"], "PYTHONPATH": str(snapshot)}
    before = subprocess.run(
        [sys.executable, "-c", _READ_LEGACY], cwd=snapshot,
        env=environment, capture_output=True, text=True, check=True,
    ).stdout
    raw = json.loads(before)
    raw["generated_at"] = datetime(2026, 1, 1, tzinfo=timezone.utc)
    legacy = SemanticLayerDocument.model_validate(raw)
    candidate = {metric.name: metric for metric in active.metrics}
    assert set(candidate) == {metric.name for metric in legacy.metrics}
    for region in [None, "North", "South", "Missing"]:
        for metric in legacy.metrics:
            actual: list[list[dict[str, object]]] = []
            for formula in [metric.formula_sql, candidate[metric.name].formula_sql]:
                sql = sqlglot.parse_one(
                    f'SELECT {formula} AS value FROM "Orders"', read="postgres",
                ).sql(dialect="postgres", identify=True)
                if region is not None:
                    sql += ' WHERE "Region" = %s'
                with reference_connection.cursor() as cur:
                    cur.execute(sql, (region,) if region is not None else None)
                    actual.append(cur.fetchall())
            assert actual[0] == actual[1]
    after = subprocess.run(
        [sys.executable, "-c", _READ_LEGACY], cwd=snapshot,
        env=environment, capture_output=True, text=True, check=True,
    ).stdout
    assert after == before
    _dictionary, _table, returned, returned_text = cli._load_semantic_context()
    assert returned.provenance is not None
    assert returned.provenance.model_sha256 == active_hash
    assert returned_text == active_text
    assert returned.metrics == active.metrics
