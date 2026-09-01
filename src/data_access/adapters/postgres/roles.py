"""PostgreSQL helper: ensure the `metabase_readonly` role exists.

Engine-specific code confined to the PG adapter package (constitution
Principle III). The role grants `SELECT` on existing tables + as the
default privilege for future tables created in the `public` schema — that
way, if you re-bootstrap, Metabase still gets SELECT on the new tables.

Reference: specs/004-metabase-integration/research.md Part C
            specs/004-metabase-integration/tasks.md T006
"""

from __future__ import annotations

from psycopg.sql import Identifier, SQL, Literal

from src.data_access.adapters.postgres.connection import DictConnection

_ROLE_NAME = "metabase_readonly"


def ensure_metabase_readonly_role(conn: DictConnection, password: str) -> None:
    """Idempotently create a read-only PG role for Metabase.

    - CREATE ROLE if it doesn't exist (with LOGIN + PASSWORD).
    - GRANT SELECT on all tables in `public`.
    - ALTER DEFAULT PRIVILEGES so future tables in `public` are also SELECTable
      by this role (defense against re-bootstrap scenarios).

    Args:
        conn: an open `DictConnection` to the warehouse DB (admin user).
        password: the password to assign to the metabase_readonly role.

    Raises:
        psycopg.Error on DB failure — the caller treats this as setup failure
        (metabase setup would surface a clear error, FR-013 / FR-005).
    """
    with conn.cursor() as cur:
        # Check if the role exists.
        cur.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (_ROLE_NAME,),
        )
        if cur.fetchone() is None:
            # CREATE ROLE — DDL cannot use server-side bind parameters for
            # PASSWORD (same psycopg quirk as baseline's _render_column_ddl).
            # Use psycopg.sql.Literal to safely compose the password literal.
            cur.execute(
                SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    Identifier(_ROLE_NAME), Literal(password)
                )
            )
        else:
            # Role exists — update the password (idempotency: the password
            # might have changed since the last run).
            cur.execute(
                SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    Identifier(_ROLE_NAME), Literal(password)
                )
            )

        # Always (re)grant SELECT on existing tables in `public` to be safe.
        cur.execute(
            SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                Identifier(_ROLE_NAME)
            )
        )

        # Future tables created in `public` should also be readable.
        cur.execute(
            SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT ON TABLES TO {}"
            ).format(Identifier(_ROLE_NAME))
        )
    conn.commit()


__all__ = ["ensure_metabase_readonly_role"]
