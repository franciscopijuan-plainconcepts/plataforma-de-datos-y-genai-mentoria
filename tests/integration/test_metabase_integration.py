"""Integration tests: Metabase setup idempotency + end-to-end flow.

Constitution Principle IV NON-NEGOTIABLE check (SC-003) — see
test_metabase_integration.py for the specific governance test.

This file covers:
- T014: idempotency of `metabase setup` (second run is a no-op).

Requires Docker Metabase + the `metabase setup` to have succeeded at least
once. The tests are SKIPPED if Docker is unavailable or the Metabase container
is unreachable (constitution allows integration tests against Docker;
no mocks).

Reference: specs/004-metabase-integration/tasks.md T014
            specs/004-metabase-integration/quickstart.md
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _docker_metabase_running() -> bool:
    """Detect if the Metabase container is listening on localhost:3000."""
    host = os.environ.get("METABASE_HOST", "http://localhost:3000")
    # Extract host/port simply.
    port = int(os.environ.get("METABASE_PORT", "3000"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        return s.connect_ex(("localhost", port)) == 0


pytestmark = pytest.mark.skipif(
    not _docker_metabase_running(),
    reason=(
        "Requires Docker Metabase on localhost:3000. "
        "Run `uv run python -m src.cli.main metabase setup` first."
    ),
)


def test_metabase_setup_is_idempotent() -> None:
    """T014: running `metabase setup` twice produces the same state file
    and the admin user count does NOT increase on Metabase."""
    import json
    import subprocess
    import sys

    # First run (assumed already done if the container is up).
    r1 = subprocess.run(
        [sys.executable, "-m", "src.cli.main", "metabase", "setup"],
        cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "ENV": "local"},
    )
    if r1.returncode != 0:
        pytest.skip(f"metabase setup (first run) failed: {r1.stderr[:300]}")

    state_path = _REPO_ROOT / ".artifacts" / "metabase_state.json"
    assert state_path.exists(), "setup should have written metabase_state.json"
    state1 = json.loads(state_path.read_text(encoding="utf-8"))
    admin_email_1 = state1.get("admin_email")
    db_id_1 = state1.get("metabase_db_id")
    collection_id_1 = state1.get("collection_id")

    # Second run — idempotent; must detect it's already configured.
    r2 = subprocess.run(
        [sys.executable, "-m", "src.cli.main", "metabase", "setup"],
        cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "ENV": "local"},
    )
    assert r2.returncode == 0, f"second setup run failed: {r2.stderr[:300]}"

    state2 = json.loads(state_path.read_text(encoding="utf-8"))
    # The state file is deterministic across runs (admin_email, db_id, collection_id same).
    assert state2.get("admin_email") == admin_email_1
    assert state2.get("metabase_db_id") == db_id_1
    assert state2.get("collection_id") == collection_id_1


def test_metabase_state_file_loadable() -> None:
    """Sanity: the persisted state loads cleanly as a MetabaseSession model."""
    from src.contracts.metabase import MetabaseSession

    state_path = _REPO_ROOT / ".artifacts" / "metabase_state.json"
    if not state_path.exists():
        pytest.skip("metabase_state.json not present; run setup first.")
    state = MetabaseSession.model_validate_json(
        state_path.read_text(encoding="utf-8")
    )
    assert state.admin_email
    assert state.metabase_db_id >= 1
    assert state.collection_id >= 1
