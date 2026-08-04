"""Integration test for warehouse provisioning (constitution-mandated).

Runs the full `bootstrap` flow (Phase 3: EDA → schema → load → manifest)
against the Dockerized PostgreSQL — NO MOCKS for the governance/db path
(per constitution Dev Workflow Quality Gates).

Asserts:
- All three tables (Orders, Returns, People) are present.
- Row counts match the EDA-derived canonical counts: 51,290 / 2,033 / 24.
- The load manifest is written and its source_sha256 matches the .xlsx.

This test REQUIRES a running Docker daemon + the `bootstrap` CLI. It skips
gracefully (not fails) when Docker is unavailable, so `pytest` still passes
in CI environments without Docker.

Reference: specs/001-data-genai-platform-baseline/spec.md (FR-003, SC-002)
            specs/001-data-genai-platform-baseline/quickstart.md § Validation
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.data_access.adapters.postgres.connection import PostgresConfig
from src.data_access.adapters.postgres.repository import PostgresRepository
from src.data_engineering.ingestion.manifest import sha256_of_file


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_FILE = _REPO_ROOT / "Global Superstore Data.xlsx"
_MANIFEST_PATH = _REPO_ROOT / ".artifacts" / "load_manifest.json"

_EXPECTED = {
    "Orders": 51290,
    "Returns": 2033,
    "People": 24,
}


def _docker_available() -> bool:
    """True if the Docker daemon is installed and responsive."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, check=False, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# Skip the whole module gracefully when Docker isn't running.
pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not running — bootstrap integration test requires Dockerized PostgreSQL.",
)


@pytest.fixture(scope="module")
def bootstrapped_warehouse() -> None:
    """Run `bootstrap` once for the module, ensuring a clean loaded warehouse."""
    result = subprocess.run(
        ["uv", "run", "python", "-m", "src.cli.main", "bootstrap"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(f"bootstrap failed:\n{result.stderr}")


def test_three_tables_present_with_expected_row_counts(
    bootstrapped_warehouse: None,
) -> None:
    """FR-003 / SC-002: exactly Orders/Returns/People exist with the right counts."""
    config = PostgresConfig.from_env()
    with PostgresRepository(config=config) as repo:
        tables = set(repo.list_tables())
        for table_name, expected_count in _EXPECTED.items():
            assert table_name in tables, f"Table {table_name!r} is missing from the warehouse"
            actual = repo.count_rows(table_name)
            assert actual == expected_count, (
                f"{table_name}: expected {expected_count} rows, got {actual}"
            )


def test_load_manifest_written_and_source_hash_matches(
    bootstrapped_warehouse: None,
) -> None:
    """FR-014: the manifest is present and its source_sha256 matches the .xlsx."""
    import json

    assert _MANIFEST_PATH.exists(), "Load manifest was not written by bootstrap"
    manifest = json.loads(_MANIFEST_PATH.read_text())
    expected_sha = sha256_of_file(_SOURCE_FILE)
    assert manifest["source_sha256"] == expected_sha, (
        "Manifest source_sha256 does not match the source .xlsx hash"
    )
    # Per-table row counts in the manifest MUST match the expected counts.
    for entry in manifest["per_table"]:
        name = entry["table_name"]
        if name in _EXPECTED:
            assert entry["row_count"] == _EXPECTED[name], (
                f"Manifest {name} row_count={entry['row_count']} "
                f"!= expected {_EXPECTED[name]}"
            )
