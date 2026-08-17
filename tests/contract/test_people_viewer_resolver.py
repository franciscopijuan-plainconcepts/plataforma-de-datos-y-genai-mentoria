"""Contract tests for the PeopleViewerResolver (v2.0 "login as person" model).

Asserts:
- `PeopleViewerResolver.resolve()` returns a `SemanticViewer` with the region
  attached to a real person in the `People` table.
- Multiple lookup shapes work: snake_case ID, full name with accents,
  de-accented name.
- The `viewer_id` is normalized to snake_case.
- Unknown viewer values return None (caller handles fallback).
- The `list_available()` returns only the normalized IDs (not raw names).

Reference: src/data_engineering/semantic_layer/person_resolver.py
"""

from __future__ import annotations

import pytest

from src.contracts.data_access import TableDef, ColumnDef, LogicalType
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import QueryRow
from src.data_engineering.semantic_layer.person_resolver import (
    PeopleViewerResolver,
    _normalize,
    _strip_accents,
)


# --- Minimal in-memory People fixture for tests (no Docker PG required) ---


class _FakePeopleProvider:
    """A fake QueryProvider that simulates the People table with 3 fixture rows."""

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        # rows: [(person_name, region_name), ...]
        self._rows = rows
        self._calls: int = 0

    def execute_readonly_query(self, sql: str, table_def: TableDef) -> list[QueryRow]:
        from src.contracts.text_to_sql import QueryRow

        self._calls += 1
        # Return the static fixture regardless of the input SQL (it's only
        # ever queried with `SELECT "Person", "Region" FROM "People"`).
        return [
            QueryRow(data={"Person": person, "Region": region})
            for person, region in self._rows
        ]


_PEOPLE_FIXTURE: list[tuple[str, str]] = [
    ("Marilène Rousseau", "Caribbean"),
    ("Andile Ihejirika", "Central Africa"),
    ("Flannery Newton", "Southern US"),
    ("Angela Jephson", "Western Canada"),  # the mismatch region (no Orders match)
]


@pytest.fixture()
def people_resolver() -> PeopleViewerResolver:
    provider = _FakePeopleProvider(_PEOPLE_FIXTURE)
    return PeopleViewerResolver(query_provider=provider, is_local_dev=True)


# --- Normalization helpers ---


def test_strip_accents_works() -> None:
    assert _strip_accents("Marilène") == "Marilene"
    assert _strip_accents("Rousseau") == "Rousseau"


def test_normalize_snake_case() -> None:
    assert _normalize("Marilène Rousseau") == "marilene_rousseau"
    assert _normalize("Andile Ihejirika") == "andile_ihejirika"
    assert _normalize("  Flannery  Newton  ") == "flannery_newton"


# --- resolve(): multiple lookup shapes ---


def test_resolve_by_snake_case_id(people_resolver: PeopleViewerResolver) -> None:
    """Login with the normalized ID (e.g., `--viewer marilene_rousseau`)."""
    v = people_resolver.resolve("marilene_rousseau")
    assert v is not None
    assert v.viewer_id == "marilene_rousseau"
    assert v.regions == ["Caribbean"]


def test_resolve_by_full_name_with_accent(people_resolver: PeopleViewerResolver) -> None:
    """Login with the full name as it appears in People (`Marilène Rousseau`)."""
    v = people_resolver.resolve("Marilène Rousseau")
    assert v is not None
    assert v.viewer_id == "marilene_rousseau"
    assert v.regions == ["Caribbean"]


def test_resolve_by_deaccented_name(people_resolver: PeopleViewerResolver) -> None:
    """Login with the name without accents (`Marilene Rousseau`)."""
    v = people_resolver.resolve("Marilene Rousseau")
    assert v is not None
    assert v.viewer_id == "marilene_rousseau"
    assert v.regions == ["Caribbean"]


# --- resolve(): unknown viewer returns None ---


def test_resolve_unknown_returns_none(people_resolver: PeopleViewerResolver) -> None:
    """Unknown viewer value: caller falls back to viewers.yaml (handled in CLI)."""
    assert people_resolver.resolve("charlie_unknown") is None


def test_can_resolve_returns_true_for_known(people_resolver: PeopleViewerResolver) -> None:
    assert people_resolver.can_resolve("marilene_rousseau") is True
    assert people_resolver.can_resolve("Marilène Rousseau") is True


def test_can_resolve_returns_false_for_unknown(people_resolver: PeopleViewerResolver) -> None:
    assert people_resolver.can_resolve("charlie_unknown") is False


# --- list_available(): only normalized IDs, sorted ---


def test_list_available_returns_normalized_ids(people_resolver: PeopleViewerResolver) -> None:
    available = people_resolver.list_available()
    assert available == [
        "andile_ihejirika",
        "angela_jephson",
        "flannery_newton",
        "marilene_rousseau",
    ]


# --- Caching: only one People query is made ---


def test_cache_avoids_repeated_queries(people_resolver: PeopleViewerResolver) -> None:
    """The first resolve() populates the in-memory cache; subsequent calls don't re-query."""
    # Access the underlying fake provider to count calls.
    underlying = _FakePeopleProvider(_PEOPLE_FIXTURE)
    resolver = PeopleViewerResolver(query_provider=underlying, is_local_dev=True)

    resolver.resolve("marilene_rousseau")
    resolver.resolve("andile_ihejirika")
    resolver.resolve("flannery_newton")
    assert underlying._calls == 1  # only one read against People


# --- The returned viewer is a real SemanticViewer ---


def test_resolved_viewer_is_pydantic_model(people_resolver: PeopleViewerResolver) -> None:
    v = people_resolver.resolve("marilene_rousseau")
    assert isinstance(v, SemanticViewer)
    # is_local_dev propagates from constructor.
    assert v.is_local_dev is True
    # allows_full_access defaults to False (People entries are real viewers).
    assert v.allows_full_access is False
