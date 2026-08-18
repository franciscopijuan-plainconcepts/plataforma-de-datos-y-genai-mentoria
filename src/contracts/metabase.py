"""Metabase integration contract models (Pydantic v2).

Defines the typed contracts that flow between the `MetabaseClient`
(`src/ai_engineering/metabase_client.py`) and the rest of the platform:
- `MetabaseConfig`  — env-based configuration (URL + admin creds).
- `Card`             — a Metabase native-query card created from a governed SQL.
- `Collection`       — a logical collection (folder) in Metabase.
- `Dashboard`        — a dashboard aggregating multiple cards.
- `DashboardItem`    — a card placed on a dashboard (dashcard).
- `MetabaseSession` — the persisted state of `metabase setup` (idempotency cache).

These models are the SOLE currency crossing the Metabase boundary. The
`MetabaseClient` is the ONLY module that may import `httpx` — boundary test
enforced. The `TextToSqlPipeline` does NOT reference Metabase directly; it
receives a generic `on_query_complete` callback via constructor injection
(see `contracts/pipeline_integration.md`).

Reference: specs/004-metabase-integration/data-model.md
            specs/004-metabase-integration/contracts/metabase_client.md
            specs/004-metabase-integration/contracts/pipeline_integration.md
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Display type is a small closed set dictated by the Metabase API v0.x.
CardDisplay = Literal["scalar", "table", "bar", "line", "area"]
"""
The chart type Metabase uses to render a card's result set. Chosen by
heuristics in `MetabaseClient._infer_display_type` (see research.md Part E):
- scalar : single-value totals (1 row × 1 col).
- bar    : GROUP BY over a categorical dimension with ≤20 rows.
- table  : default safe fallback.
"""


class MetabaseConfig(BaseModel):
    """Configuration for the Metabase client, loaded from environment variables.

    Loaded via `MetabaseConfig.from_env()`. The admin password is never logged
    (FR-008); the session token produced by `login()` is held in-memory only.
    """

    model_config = ConfigDict(frozen=True)

    host: str
    admin_email: str
    admin_password: str
    port: int = 3000
    collection_name: str = "Chat Sessions"
    db_name: str = "Plataforma PostgreSQL"

    @classmethod
    def from_env(cls) -> "MetabaseConfig":
        """Build config from METABASE_* env vars.

        Raises ValueError if METABASE_ADMIN_EMAIL or METABASE_ADMIN_PASSWORD
        are missing or empty (FR-013 fail-fast).
        """
        host = os.environ.get("METABASE_HOST", "http://localhost:3000")
        admin_email = os.environ.get("METABASE_ADMIN_EMAIL", "")
        admin_password = os.environ.get("METABASE_ADMIN_PASSWORD", "")
        if not admin_email.strip() or not admin_password.strip():
            raise ValueError(
                "METABASE_ADMIN_EMAIL and METABASE_ADMIN_PASSWORD must both be "
                "set in .env (see .env.example). The Metabase integration "
                "cannot run without them."
            )
        port_str = os.environ.get("METABASE_PORT", "3000")
        try:
            port = int(port_str)
        except ValueError as exc:
            raise ValueError(
                f"METABASE_PORT={port_str!r} is not a valid integer"
            ) from exc
        return cls(
            host=host,
            admin_email=admin_email,
            admin_password=admin_password,
            port=port,
        )


class Card(BaseModel):
    """A Metabase card (native SQL query) created by the pipeline.

    The `sql` field carries the **already-governed SQL** (i.e., post-
    `SemanticQueryResolver.apply_rls`, with `WHERE "Region" IN (...)` injected).
    This is the constitutionally-critical invariant: Metabase never sees the
    pre-governed SQL (Principle IV NON-NEGOTIABLE preserved by design).
    """

    model_config = ConfigDict(frozen=True)

    id: int
    name: str = Field(max_length=140)
    sql: str = Field(min_length=1)
    collection_id: int
    display: CardDisplay
    description: str | None = None
    created_at: datetime | None = None


class Collection(BaseModel):
    """A Metabase collection (folder) where cards live."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    parent_id: int | None = None
    location: str = "/"


class DashboardItem(BaseModel):
    """A card placed on a dashboard (Metabase calls this a dashcard)."""

    model_config = ConfigDict(frozen=True)

    id: int
    card_id: int
    dashboard_id: int


class Dashboard(BaseModel):
    """A Metabase dashboard aggregating multiple cards."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    collection_id: int
    ordered_items: list[DashboardItem] = []


class MetabaseSession(BaseModel):
    """Persisted state of `metabase setup`, cached in `.artifacts/metabase_state.json`.

    Used by subsequent CLI commands (`metabase status`, `metabase reset-cards`)
    so they don't re-query the Metabase API for the collection_id / db_id.
    """

    model_config = ConfigDict(frozen=True)

    configured_at: datetime
    admin_email: str
    metabase_db_id: int
    collection_id: int
    metabase_version: str | None = None


__all__ = [
    "CardDisplay",
    "MetabaseConfig",
    "Card",
    "Collection",
    "DashboardItem",
    "Dashboard",
    "MetabaseSession",
]
