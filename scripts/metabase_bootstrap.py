"""Metabase bootstrap script (v2.1 — v0.58 LTS API compatibility).

This script does what `metabase setup` cannot do automatically yet due to
Metabase v0.58's stricter API validation. It:

1. Fetches the setup-token from GET /api/session/properties.
2. POSTs /api/setup to create the admin user (or skips if already done).
3. Logs in with the admin credentials.
4. Creates the PostgreSQL DB connection (using metabase_readonly role).
5. Creates the "Chat Sessions" collection.
6. Persists the state to .artifacts/metabase_state.json.
7. Ensures the metabase_readonly PG role has the correct password + grants.

Usage:
    uv run python scripts/metabase_bootstrap.py

Prerequisites:
    - Docker Metabase running on localhost:3000 (healthcheck OK).
    - .env with METABASE_ADMIN_EMAIL, METABASE_ADMIN_PASSWORD, POSTGRES_*.

Reference: specs/004-metabase-integration/research.md Parts F, G, C.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import httpx
from psycopg.sql import Identifier, Literal, SQL


# --- Load .env ---
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env", override=True)

METABASE_HOST = os.environ.get("METABASE_HOST", "http://localhost:3000")
ADMIN_EMAIL = os.environ.get("METABASE_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("METABASE_ADMIN_PASSWORD", "")
STATE_PATH = _REPO_ROOT / ".artifacts" / "metabase_state.json"


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def step1_fetch_setup_token() -> str | None:
    """Fetch the setup-token from GET /api/session/properties."""
    print("Step 1: Fetching setup-token...")
    resp = httpx.get(f"{METABASE_HOST}/api/session/properties", verify=False, timeout=10)
    resp.raise_for_status()
    props = resp.json()
    token = props.get("setup-token")
    has_user = props.get("has-user-setup")
    if has_user:
        print("  Admin user already exists; will skip setup.")
        return None
    if not token:
        print("  No setup-token and no user — unexpected state. Will try login.")
        return None
    _ok(f"setup-token: {token[:20]}...")
    return token


def step2_create_admin(token: str | None) -> None:
    """POST /api/setup to create the admin user (idempotent)."""
    print("Step 2: Creating admin user...")
    if token is None:
        _ok("Skipped (admin user already exists).")
        return
    body = {
        "token": token,
        "user": {
            "email": ADMIN_EMAIL,
            "first_name": "Admin",
            "last_name": "Plataforma",
            "password": ADMIN_PASSWORD,
            "site_name": "Plataforma de Datos y GenAI",
        },
        "prefs": {
            "site_name": "Plataforma de Datos y GenAI",
            "site_locale": "en",
        },
    }
    resp = httpx.post(f"{METABASE_HOST}/api/setup", json=body, verify=False, timeout=10)
    if resp.status_code == 200:
        _ok("Admin user created.")
    elif resp.status_code == 403:
        _ok("Admin user already exists (403 — OK).")
    else:
        _fail(f"setup failed: HTTP {resp.status_code} — {resp.text[:300]}")


def step3_login() -> str:
    """Login and return the session token."""
    print("Step 3: Logging in...")
    resp = httpx.post(
        f"{METABASE_HOST}/api/session",
        json={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        verify=False,
        timeout=10,
    )
    if resp.status_code != 200:
        _fail(f"login failed: HTTP {resp.status_code} — {resp.text[:200]}")
    token = resp.json().get("id")
    if not token:
        _fail("login response missing 'id' (session token).")
    _ok(f"session: {token[:20]}...")
    return token


def step4_create_db_connection(session: str) -> int:
    """Create the PostgreSQL DB connection (or reuse existing)."""
    print("Step 4: Creating DB connection...")
    headers = {"X-Metabase-Session": session}

    # Check if "Plataforma PostgreSQL" already exists
    list_resp = httpx.get(
        f"{METABASE_HOST}/api/database?limit=50", headers=headers, verify=False, timeout=10
    )
    list_resp.raise_for_status()
    list_data = list_resp.json()
    existing = list_data.get("data", list_data) if isinstance(list_data, dict) else list_data
    for db in existing:
        if db.get("name") == "Plataforma PostgreSQL":
            db_id = int(db.get("id", 0))
            _ok(f"DB connection already exists (id={db_id}).")
            return db_id

    # Create new
    body = {
        "engine": "postgres",
        "name": "Plataforma PostgreSQL",
        "details": {
            "host": "postgres",
            "port": 5432,
            "dbname": "global_superstore",
            "user": "metabase_readonly",
            "password": ADMIN_PASSWORD,
            "ssl": False,
        },
    }
    resp = httpx.post(
        f"{METABASE_HOST}/api/database", json=body, headers=headers, verify=False, timeout=15
    )
    if resp.status_code != 200:
        _fail(f"create_db failed: HTTP {resp.status_code} — {resp.text[:300]}")
    db_id = int(resp.json().get("id", 0))
    _ok(f"DB connection created (id={db_id}).")
    return db_id


def step5_create_collection(session: str) -> int:
    """Create the "Chat Sessions" collection (or reuse existing)."""
    print("Step 5: Creating 'Chat Sessions' collection...")
    headers = {"X-Metabase-Session": session}

    # Check if it already exists
    list_resp = httpx.get(
        f"{METABASE_HOST}/api/collection", headers=headers, verify=False, timeout=10
    )
    list_resp.raise_for_status()
    for coll in list_resp.json():
        if coll.get("name") == "Chat Sessions":
            coll_id = int(coll.get("id", 0))
            _ok(f"Collection already exists (id={coll_id}).")
            return coll_id

    # Create new
    resp = httpx.post(
        f"{METABASE_HOST}/api/collection",
        json={"name": "Chat Sessions"},
        headers=headers,
        verify=False,
        timeout=10,
    )
    if resp.status_code != 200:
        _fail(f"create_collection failed: HTTP {resp.status_code} — {resp.text[:200]}")
    coll_id = int(resp.json().get("id", 0))
    _ok(f"Collection created (id={coll_id}).")
    return coll_id


def step6_persist_state(db_id: int, coll_id: int) -> None:
    """Persist the MetabaseSession state to .artifacts/metabase_state.json."""
    print("Step 6: Persisting state...")
    state = {
        "configured_at": datetime.now(timezone.utc).isoformat(),
        "admin_email": ADMIN_EMAIL,
        "metabase_db_id": db_id,
        "collection_id": coll_id,
        "metabase_version": "v0.58.31",
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _ok(f"State persisted to {STATE_PATH}.")


def step7_ensure_pg_role() -> None:
    """Ensure the metabase_readonly PG role exists with correct password + grants."""
    print("Step 7: Ensuring metabase_readonly PG role...")
    from src.data_access.adapters.postgres.connection import PostgresConfig, connect

    config = PostgresConfig.from_env()
    conn = connect(config)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", ("metabase_readonly",))
        if cur.fetchone() is None:
            cur.execute(
                SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    Identifier("metabase_readonly"), Literal(ADMIN_PASSWORD)
                )
            )
            _ok("Role metabase_readonly created.")
        else:
            cur.execute(
                SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    Identifier("metabase_readonly"), Literal(ADMIN_PASSWORD)
                )
            )
            _ok("Role metabase_readonly password updated.")
        cur.execute(
            SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                Identifier("metabase_readonly")
            )
        )
        cur.execute(
            SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT ON TABLES TO {}"
            ).format(Identifier("metabase_readonly"))
        )
    conn.commit()
    conn.close()
    _ok("SELECT grants applied.")


def main() -> None:
    print("=" * 60)
    print("Metabase Bootstrap (v0.58 LTS API compatibility)")
    print("=" * 60)

    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        _fail(
            "METABASE_ADMIN_EMAIL and METABASE_ADMIN_PASSWORD must be set in .env "
            "(see .env.example)."
        )

    # Step 0: Check Metabase is reachable
    print("Step 0: Checking Metabase health...")
    try:
        health = httpx.get(f"{METABASE_HOST}/api/health", verify=False, timeout=10)
        if health.status_code != 200:
            _fail(f"Metabase not healthy (HTTP {health.status_code}). Is Docker running?")
        _ok("Metabase is healthy.")
    except Exception as exc:
        _fail(f"Cannot reach Metabase at {METABASE_HOST}: {exc}")

    # Step 7: PG role (must run BEFORE step 4 so the DB connection password works)
    step7_ensure_pg_role()

    # Steps 1-6: API setup
    token = step1_fetch_setup_token()
    step2_create_admin(token)
    session = step3_login()
    db_id = step4_create_db_connection(session)
    coll_id = step5_create_collection(session)
    step6_persist_state(db_id, coll_id)

    # Step 7: PG role
    step7_ensure_pg_role()

    print()
    print("=" * 60)
    print("Metabase bootstrap complete!")
    print(f"  URL           : {METABASE_HOST}")
    print(f"  Admin email   : {ADMIN_EMAIL}")
    print(f"  DB connection : id={db_id} (PostgreSQL via metabase_readonly)")
    print(f"  Collection    : id={coll_id} ('Chat Sessions')")
    print(f"  State         : {STATE_PATH}")
    print()
    print(f"Open {METABASE_HOST} and login with the admin credentials.")
    print("=" * 60)


if __name__ == "__main__":
    main()
