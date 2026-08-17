"""Integration test: RLS enforcement end-to-end against the Dockerized PostgreSQL.

Constitution Principle IV NON-NEGOTIABLE check: two viewers with different
region scopes MUST receive different result sets when asking the same question.
If a viewer with regions=[R1] returns rows from region R2 (or vice versa), the
test fails — that would mean governance is being bypassed.

Requires:
- Dockerized PostgreSQL (run `uv run python -m src.cli.main bootstrap` first).
- `FORGE_API_KEY` in `.env` (the test calls the LLM via the Forge proxy).
- `viewers.yaml` defining `alice` (Caribbean + Central America) and `bob`
  (Central US). The committed `viewers.example.yaml` has both; copy it:
  `cp viewers.example.yaml viewers.yaml`.

The test is SKIPPED without these preconditions (constitution allows integration
tests against the Dockerized PG; we don't mock the database for governance
enforcement paths).

Reference: specs/003-semantic-layer-v1/quickstart.md §A2/A3
            specs/003-semantic-layer-v1/tasks.md T020
            specs/003-semantic-layer-v1/spec.md SC-002 / SC-008
"""

from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_VIEWERS_YAML = _REPO_ROOT / "viewers.yaml"
_DOCKER_TIMEOUT_S = 5


def _docker_pg_running() -> bool:
    """Detect if the Dockerized PostgreSQL is reachable."""
    import os
    import socket

    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(_DOCKER_TIMEOUT_S)
        return s.connect_ex(("localhost", port)) == 0


def _forge_available() -> bool:
    import os

    key = os.environ.get("FORGE_API_KEY", "").strip()
    return bool(key)


pytestmark = pytest.mark.skipif(
    not _docker_pg_running() or not _forge_available() or not _VIEWERS_YAML.exists(),
    reason=(
        "Requires Dockerized PostgreSQL + FORGE_API_KEY + viewers.yaml. "
        "Run `bootstrap`, set FORGE_API_KEY in .env, and "
        "`cp viewers.example.yaml viewers.yaml`."
    ),
)


def _direct_pg_sum(region_filter_values: list[str]) -> float | None:
    """Connect directly to PG and run `SELECT SUM("Sales") WHERE "Region" IN (...)`.

    Returns None if the query returns no rows or errors. This is the GROUND TRUTH
    the governed Text-to-SQL call should match.
    """
    from src.data_access.adapters.postgres.connection import PostgresConfig
    from src.data_access.adapters.postgres.repository import PostgresRepository

    quoted = ", ".join(f"'{r}'" for r in region_filter_values)
    sql = f'SELECT SUM("Sales") AS total FROM Orders WHERE "Region" IN ({quoted})'
    config = PostgresConfig.from_env()
    with PostgresRepository(config=config) as repo:
        # type: ignore[operator] — repo satisfies QueryProvider at runtime.
        rows = repo.execute_readonly_query(sql, table_def=None)  # type: ignore[arg-type]
    if not rows:
        return None
    return rows[0].data.get("total")


def test_two_viewers_return_different_totals() -> None:
    """SC-002 / SC-008: two viewers scoped to different regions return different SUM(Sales).

    Run the governed Text-to-SQL pipeline once per viewer and confirm:
    - Alice's total equals the direct PG query with her regions.
    - Bob's total equals the direct PG query with his regions.
    - The two totals differ (no bypass — bypass would make them identical).
    """
    import os
    import subprocess
    import sys

    if not _docker_pg_running() or not _forge_available() or not _VIEWERS_YAML.exists():
        pytest.skip("Preconditions not met")

    alice_regions = ["Caribbean", "Central America"]
    bob_regions = ["Central US"]

    # Ground truth directly from PG (no governance, no LLM).
    alice_truth = _direct_pg_sum(alice_regions)
    bob_truth = _direct_pg_sum(bob_regions)

    # Sanity: the truth values themselves should differ.
    assert alice_truth != bob_truth, (
        "Setup inconsistency: alice and bob truth totals are equal — the test "
        "regions must be distinct for the diff assertion to be meaningful."
    )

    # Run the governed Text-to-SQL pipeline via the CLI for alice.
    alice_result = _run_ask_via_cli(viewer="alice", question="What is the total sales amount?")
    bob_result = _run_ask_via_cli(viewer="bob", question="What is the total sales amount?")

    assert alice_result is not None, "Alice's ask command returned no rows or errored"
    assert bob_result is not None, "Bob's ask command returned no rows or errored"

    # The governed totals MUST match the direct PG filter (within float tolerance).
    # `Decimal` -> float conversion; use a small epsilon.
    def _to_float(v: object) -> float:
        from decimal import Decimal

        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (int, float)):
            return float(v)
        return 0.0

    alice_governed = _to_float(alice_result)
    bob_governed = _to_float(bob_result)
    alice_truth_f = _to_float(alice_truth)
    bob_truth_f = _to_float(bob_truth)

    # Tolerance: 0.01 (two-decimal money comparison).
    assert abs(alice_governed - alice_truth_f) < 0.01, (
        f"Alice's governed total ({alice_governed}) does not match ground truth "
        f"({alice_truth_f}) for regions {alice_regions}. Governance may be bypassing RLS."
    )
    assert abs(bob_governed - bob_truth_f) < 0.01, (
        f"Bob's governed total ({bob_governed}) does not match ground truth "
        f"({bob_truth_f}) for regions {bob_regions}. Governance may be bypassing RLS."
    )

    # CRITICAL: the two governed totals MUST differ — any bypass would make them equal
    # (running without RLS would return the same full-database total for both).
    assert abs(alice_governed - bob_governed) > 0.01, (
        "Alice's and Bob's governed totals are equal — RLS is NOT being enforced!"
        f" alice={alice_governed}, bob={bob_governed}. Constitution Principle IV violation."
    )


def _run_ask_via_cli(viewer: str, question: str) -> object | None:
    """Invoke the `ask` CLI with --viewer and capture the SUM value from stdout.

    The CLI prints a row dict like `{'total': Decimal('1234.56')}`. This helper
    parses it loosely (we just want the numeric value to compare).
    """
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["ENV"] = "local"  # required so --viewer can be honored

    cmd = [
        sys.executable, "-m", "src.cli.main", "ask",
        "--viewer", viewer,
        question,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    # The CLI prints: `Rows (1):` then `  {'total': Decimal('...')}`.
    # Extract the numeric value with a simple parse.
    out = result.stdout
    # Find a row line like `  {'total': Decimal('1234.56')}` or `  {'total': 1234.56}`.
    import re

    m = re.search(r"'total':\s*(?:Decimal\()?([\d.]+)\)?", out)
    if m is None:
        return None
    return float(m.group(1))


def test_log_includes_viewer_id_and_gov_bypass_flag() -> None:
    """FR-021: the structured log line must include viewer_id and gov_bypass flag.

    After running `ask --viewer alice`, the log line should contain
    `viewer_id=alice` and `gov_bypass=False`.
    """
    import re

    log_path = _REPO_ROOT / ".artifacts" / "text_to_sql.log"
    if not log_path.exists():
        pytest.skip("text_to_sql.log not present — run a governed `ask` first")

    # Read the last ~5 lines (we want the most recent invocation).
    lines = log_path.read_text(encoding="utf-8").splitlines()[-5:]
    joined = "\n".join(lines)
    assert "viewer_id=" in joined, (
        f"log line missing viewer_id field (FR-021). Recent lines:\n{joined}"
    )
    assert "gov_bypass=" in joined, (
        f"log line missing gov_bypass flag (FR-021). Recent lines:\n{joined}"
    )
    # For alice (allows_full_access=False), gov_bypass MUST be False.
    if "viewer_id=alice" in joined:
        assert "gov_bypass=False" in joined, (
            f"alice's call should have gov_bypass=False, got:\n{joined}"
        )
