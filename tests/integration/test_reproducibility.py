"""Integration test for reproducibility (constitution-mandated).

Verifies the baseline warehouse is deterministic across re-bootstrap cycles
(FR-005 / SC-003): captures a schema + row-count snapshot, runs
`teardown` -> `bootstrap`, re-captures, and asserts the two snapshots are
identical (no schema drift, no row-count drift).

Runs against the Dockerized PostgreSQL — NO MOCKS for the governance/db
path (per constitution Dev Workflow Quality Gates). Skips gracefully when
Docker isn't available.

Reference: specs/001-data-genai-platform-baseline/spec.md (FR-005, SC-003)
            specs/001-data-genai-platform-baseline/quickstart.md § D
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.data_engineering.validation.validator import (
    capture_snapshot_from_config,
    compare_snapshots,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]

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


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not running — reproducibility test requires Dockerized PostgreSQL.",
)


def _run_cli(*args: str) -> None:
    """Run a CLI command via subprocess; fail the test on non-zero exit."""
    cmd = ["uv", "run", "python", "-m", "src.cli.main", *args]
    result = subprocess.run(
        cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0:
        pytest.fail(
            f"Command {' '.join(cmd)} failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


@pytest.fixture(scope="module")
def initialized_warehouse() -> None:
    """Ensure the warehouse is bootstrapped once before the reproducibility cycle."""
    _run_cli("bootstrap")


def test_row_counts_match_canonical_values(initialized_warehouse: None) -> None:
    """Sanity check: the live counts match the EDA-derived canonical values."""
    snap = capture_snapshot_from_config()
    for table, expected in _EXPECTED.items():
        actual = snap.row_counts.get(table)
        assert actual == expected, (
            f"{table}: expected {expected} rows, got {actual}"
        )


def test_schema_and_row_counts_stable_across_rebootstrap(
    initialized_warehouse: None,
) -> None:
    """FR-005 / SC-003: a teardown -> bootstrap cycle produces identical
    schema + row counts as the prior run (100% reproducibility)."""
    # 1. Capture the "before" snapshot from the initialized warehouse.
    before = capture_snapshot_from_config()

    # 2. Tear down (keeping nothing) and bootstrap again.
    _run_cli("teardown", "--remove-volume")
    _run_cli("bootstrap")

    # 3. Capture the "after" snapshot.
    after = capture_snapshot_from_config()

    # 4. Compare: schema and row counts MUST be identical (no drift).
    report = compare_snapshots(before, after)
    assert report.schema_matches, (
        f"Schema drift detected across re-bootstrap: {report.drift_details}"
    )
    assert report.row_counts_match, (
        f"Row-count drift detected across re-bootstrap: {report.drift_details}"
    )
    assert report.is_reproducible, (
        f"Re-bootstrap is not reproducible: {report.drift_details}"
    )


def test_row_counts_stable_across_rebootstrap(
    initialized_warehouse: None,
) -> None:
    """Explicit per-table assertion (belt-and-suspenders for SC-003): after a
    teardown -> bootstrap cycle, each table's count matches the canonical
    expected count, not just the previous run's count."""
    # The previous test already re-bootstrapped; grab the current snapshot.
    after = capture_snapshot_from_config()
    for table, expected in _EXPECTED.items():
        assert after.row_counts.get(table) == expected, (
            f"After re-bootstrap, {table} should have {expected} rows; "
            f"got {after.row_counts.get(table)}"
        )
