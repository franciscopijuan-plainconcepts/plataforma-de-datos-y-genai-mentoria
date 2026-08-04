"""psycopg v3 (sync) connection factory for the PostgreSQL adapter.

Engine-specific code confined to this package (constitution Principle III).
Upstream code MUST NOT import `psycopg` directly — it depends only on the
Protocols in `src/data_access/interfaces.py` and the contract models in
`src/contracts/data_access.py`.

Connection parameters are externalized via environment variables (FR-006);
see `.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg.rows import dict_row
from psycopg.connection import Connection

# psycopg.sql provides safe identifier composition for DDL (see research.md
# Part C "psycopg 3 server-side-binding quirk").
from psycopg.sql import Identifier

# A Connection configured with `dict_row` returns dict[str, Any] rows.
DictConnection = Connection[dict[str, object]]


@dataclass(frozen=True)
class PostgresConfig:
    """PostgreSQL connection configuration, read from environment variables."""

    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "PostgresConfig":
        """Build config from environment variables (FR-006).

        Reads POSTGRES_HOST/PORT/DB/USER/PASSWORD. Missing required values
        raise a clear, actionable error (FR-013 fail-fast).
        """
        host = os.environ.get("POSTGRES_HOST", "localhost")
        port_str = os.environ.get("POSTGRES_PORT", "5432")
        database = os.environ.get("POSTGRES_DB", "global_superstore")
        user = os.environ.get("POSTGRES_USER", "plataforma")
        password = os.environ.get("POSTGRES_PASSWORD", "plataforma_dev")
        try:
            port = int(port_str)
        except ValueError as exc:
            raise ValueError(
                f"POSTGRES_PORT={port_str!r} is not a valid integer"
            ) from exc
        return cls(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )

    @property
    def conninfo(self) -> str:
        """psycopg connection string."""
        return (
            f"host={self.host} port={self.port} dbname={self.database} "
            f"user={self.user} password={self.password}"
        )


def connect(config: PostgresConfig | None = None) -> DictConnection:
    """Open a synchronous psycopg v3 connection returning dict rows.

    Returns a `Connection[dict[str, object]]`; caller is responsible for
    closing it (use as a context manager recommended). The connection is
    configured with `dict_row` row factory so reads can be piped through
    Pydantic `model_validate`.
    """
    if config is None:
        config = PostgresConfig.from_env()
    conn = DictConnection.connect(
        conninfo=config.conninfo, row_factory=dict_row
    )
    return conn


__all__ = ["PostgresConfig", "connect", "Identifier", "DictConnection"]
