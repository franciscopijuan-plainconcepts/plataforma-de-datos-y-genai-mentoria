"""Contract tests for Metabase integration models (constitution-mandated gate).

Asserts:
- All models in `src/contracts/metabase.py` are Pydantic v2 frozen with explicit types.
- `MetabaseConfig.from_env()` fails fast on missing `METABASE_ADMIN_EMAIL`/`METABASE_ADMIN_PASSWORD`.
- `Card.sql` must be non-empty; `Card.display` Literal closed set; `Card.name` max 140 chars.
- `MetabaseSession` carries the persisted state fields.

Reference: specs/004-metabase-integration/data-model.md
            specs/004-metabase-integration/tasks.md T009
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

from src.contracts.metabase import (
    Card,
    CardDisplay,
    Collection,
    Dashboard,
    DashboardItem,
    MetabaseConfig,
    MetabaseSession,
)


# --- All models are Pydantic v2 frozen ------------------------------------


@pytest.mark.parametrize(
    "model_cls",
    [MetabaseConfig, Card, Collection, Dashboard, DashboardItem, MetabaseSession],
)
def test_models_are_pydantic_v2(model_cls: type) -> None:
    """Constitution Principle I: every model is a frozen Pydantic v2 BaseModel."""
    assert issubclass(model_cls, BaseModel), (
        f"{model_cls.__name__} must be a Pydantic BaseModel"
    )
    assert model_cls.model_config.get("frozen") is True, (
        f"{model_cls.__name__} must be frozen (immutable)"
    )


# --- MetabaseConfig.from_env() --------------------------------------------


def test_metabase_config_from_env_fails_fast_on_missing_creds() -> None:
    """FR-013: missing METABASE_ADMIN_EMAIL/PASSWORD raises before creating the client."""
    saved_email = os.environ.pop("METABASE_ADMIN_EMAIL", None)
    saved_pass = os.environ.pop("METABASE_ADMIN_PASSWORD", None)
    try:
        with pytest.raises(ValueError, match="METABASE_ADMIN_EMAIL"):
            MetabaseConfig.from_env()
    finally:
        if saved_email:
            os.environ["METABASE_ADMIN_EMAIL"] = saved_email
        if saved_pass:
            os.environ["METABASE_ADMIN_PASSWORD"] = saved_pass


def test_metabase_config_from_env_succeeds_with_creds() -> None:
    """When env vars are set, `from_env()` builds the config with the expected defaults."""
    os.environ["METABASE_ADMIN_EMAIL"] = "admin@test.local"
    os.environ["METABASE_ADMIN_PASSWORD"] = "test-pass"
    os.environ.pop("METABASE_HOST", None)
    os.environ.pop("METABASE_PORT", None)
    try:
        cfg = MetabaseConfig.from_env()
        assert cfg.host == "http://localhost:3000"  # default
        assert cfg.port == 3000
        assert cfg.admin_email == "admin@test.local"
        assert cfg.collection_name == "Chat Sessions"
        assert cfg.db_name == "Plataforma PostgreSQL"
    finally:
        os.environ.pop("METABASE_ADMIN_EMAIL", None)
        os.environ.pop("METABASE_ADMIN_PASSWORD", None)


def test_metabase_config_from_env_rejects_invalid_port() -> None:
    """A non-integer port should raise ValueError (FR-013 fail-fast)."""
    os.environ["METABASE_ADMIN_EMAIL"] = "admin@test.local"
    os.environ["METABASE_ADMIN_PASSWORD"] = "test-pass"
    os.environ["METABASE_PORT"] = "not-an-int"
    try:
        with pytest.raises(ValueError, match="METABASE_PORT"):
            MetabaseConfig.from_env()
    finally:
        os.environ.pop("METABASE_ADMIN_EMAIL", None)
        os.environ.pop("METABASE_ADMIN_PASSWORD", None)
        os.environ.pop("METABASE_PORT", None)


# --- Card model ------------------------------------------------------------


def test_card_requires_non_empty_sql() -> None:
    """The governed SQL field MUST be non-empty — an empty card is invalid."""
    with pytest.raises(ValidationError):
        Card(id=1, name="X", sql="", collection_id=1, display="table")


def test_card_display_closed_set() -> None:
    """The Literal CardDisplay closes the legal values; arbitrary display string is rejected."""
    with pytest.raises(ValidationError):
        # Intentionally passing an invalid display value; the bare type:ignore
        # silences mypy on the literal mismatch (no specific code — the error
        # rule differs between mypy versions).
        Card(id=1, name="X", sql="SELECT 1", collection_id=1, display="invalid")  # type: ignore


def test_card_name_max_140_chars() -> None:
    """Metabase truncates card names; the contract pre-validates length."""
    too_long = "x" * 200
    with pytest.raises(ValidationError):
        Card(id=1, name=too_long, sql="SELECT 1", collection_id=1, display="table")


def test_card_is_immutable() -> None:
    """Frozen models cannot be reassigned post-construction (constitution Principle I)."""
    card = Card(id=1, name="X", sql="SELECT 1", collection_id=1, display="table")
    with pytest.raises(ValidationError):
        card.sql = "SELECT 2"


# --- MetabaseSession model -------------------------------------------------


def test_metabase_session_carries_setup_state() -> None:
    """The persisted state must hold the db_id + collection_id for reuse across CLI commands."""
    state = MetabaseSession(
        configured_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        admin_email="admin@test.local",
        metabase_db_id=2,
        collection_id=4,
        metabase_version="v0.48.2",
    )
    assert state.metabase_db_id == 2
    assert state.collection_id == 4
    assert state.metabase_version == "v0.48.2"
