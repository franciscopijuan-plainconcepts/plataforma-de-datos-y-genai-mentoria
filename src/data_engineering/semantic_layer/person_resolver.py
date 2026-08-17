"""Person-based viewer resolver (v2.0 extension).

Resolves a `SemanticViewer` from a real person in the `People` table — the
"login as the person, the person already has their region" model. The
`People` table is the canonical governance mapping (constitution Principle IV),
so rather than duplicating `person -> region` entries in a hand-maintained
`viewers.yaml`, the system queries `People` directly.

Supports multiple lookup shapes so it feels like a natural login:
- The person's full name as it appears in `People`:  `Marilène Rousseau`
- A snake_case normalized ID:                        `marilene_rousseau`
- A name with spaces but no accents:                 `Marilene Rousseau`

If none of these match a row in `People`, the caller falls back to the
`ViewerRegistry` (YAML-based viewers) so escape hatches like an `admin_dev`
viewer still work without needing a People entry.

Reference: specs/003-semantic-layer-v1/spec.md (SemanticViewer)
            specs/003-semantic-layer-v1/research.md Part E (boundary enforcement)
            specs/001-data-genai-platform-baseline/data-model.md § People
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from src.contracts.data_access import TableDef
from src.contracts.semantic_layer import SemanticViewer
from src.contracts.text_to_sql import QueryRow
from src.data_access.interfaces import QueryProvider


def _strip_accents(value: str) -> str:
    """NFD-normalized accent stripping (e.g., `Marilène` -> `Marilene`)."""
    return "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )


def _normalize(value: str) -> str:
    """Lowercase, no accents, runs of whitespace and non-alnum collapsed to `_`."""
    cleaned = _strip_accents(value).lower().strip()
    # Collapse runs of whitespace/non-word chars into a single underscore.
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")


def _build_person_table_def() -> TableDef:
    """Minimal `People` TableDef used to call `execute_readonly_query`.

    The `execute_readonly_query` Protocol method takes the table_def primarily
    for column mapping; here we only need the two columns People actually has.
    """
    from src.contracts.data_access import ColumnDef, LogicalType

    return TableDef(
        name="People",
        columns=[
            ColumnDef(name="Person", logical_type=LogicalType.STRING,
                      max_length=100, nullable=False, is_primary_key=True),
            ColumnDef(name="Region", logical_type=LogicalType.STRING,
                      max_length=50, nullable=False),
        ],
        description="Regional sales people / managers (viewer→regions mapping).",
    )


class PeopleViewerResolver:
    """Resolves a `SemanticViewer` from the `People` table.

    Wraps a `QueryProvider` (the same one used elsewhere — no new adapter
    imports). The resolver does ONE read-only query against `People` the first
    time it is asked; the resulting `person -> region` map is cached in-memory
    for the lifetime of the instance so subsequent `--viewer` calls in the same
    process (e.g., `evaluate` running ~10 questions) do not re-query the DB.
    """

    def __init__(
        self,
        query_provider: QueryProvider,
        is_local_dev: bool,
        allows_full_access: bool = False,
    ) -> None:
        self._query_provider = query_provider
        self._is_local_dev = is_local_dev
        # Identity of viewers resolved via People: derived from the person's
        # normalized name so logs and audit trails show a stable snake_case id.
        self._allows_full_access_override = allows_full_access
        self._cache: dict[str, SemanticViewer] | None = None

    def _load_cache(self) -> dict[str, SemanticViewer]:
        """Populate (and memoize) the `normalized_id -> SemanticViewer` cache."""
        if self._cache is not None:
            return self._cache
        cache: dict[str, SemanticViewer] = {}
        rows: list[QueryRow] = self._query_provider.execute_readonly_query(
            'SELECT "Person", "Region" FROM "People"',
            _build_person_table_def(),
        )
        # ALSO disable RLS path: the People table is the governance mapping
        # itself; we are NOT subject to a Region filter when reading it (if
        # the caller passed through GovernedQueryProvider, the scoped Person
        # list would be filtered and we could not resolve arbitrary viewers).
        # Callers MUST invoke this resolver with an UNGOVERNED provider (the
        # raw PostgresRepository) so the full People table is accessible.
        for row in rows:
            person_raw = row.data.get("Person")
            region_raw = row.data.get("Region")
            if not isinstance(person_raw, str) or not isinstance(region_raw, str):
                continue
            viewer_id = _normalize(person_raw)
            # Allow lookup by the raw name too (e.g., `Marilène Rousseau`).
            cache[viewer_id] = SemanticViewer(
                viewer_id=viewer_id,
                regions=[region_raw],
                allows_full_access=self._allows_full_access_override and self._is_local_dev,
                is_local_dev=self._is_local_dev,
            )
            # Also index by the raw Person name (so `--viewer "Marilène Rousseau"` works).
            cache[person_raw.strip()] = cache[viewer_id]
            # And by the de-accented name (so `--viewer "Marilene Rousseau"` works).
            cache[_strip_accents(person_raw).strip()] = cache[viewer_id]
        self._cache = cache
        return cache

    def can_resolve(self, viewer_value: str) -> bool:
        """Return True if `viewer_value` matches a row in `People`."""
        cache = self._load_cache()
        keys_to_try = self._candidate_keys(viewer_value)
        return any(key in cache for key in keys_to_try)

    def resolve(self, viewer_value: str) -> SemanticViewer | None:
        """Return the `SemanticViewer` for `viewer_value`, or None if not found.

        `viewer_value` may be any of:
        - The full Person name as it appears in `People` (`Marilène Rousseau`)
        - The snake_case normalized ID (e.g., `marilene_rousseau`)
        - The name with spaces but no accents (`Marilene Rousseau`)
        """
        cache = self._load_cache()
        for key in self._candidate_keys(viewer_value):
            viewer = cache.get(key)
            if viewer is not None:
                return viewer
        return None

    def list_available(self) -> list[str]:
        """Return the sorted list of normalized viewer IDs found in `People`."""
        cache = self._load_cache()
        # Only return the normalized snake_case IDs (the canonical handles).
        return sorted(
            vid for vid, viewer in cache.items() if viewer.viewer_id == vid
        )

    @staticmethod
    def _candidate_keys(viewer_value: str) -> Iterable[str]:
        """Yield the candidate lookup keys for `viewer_value`, most-to-least strict."""
        seen: set[str] = set()
        order: list[str] = []
        for candidate in (
            _normalize(viewer_value),               # snake_case normalized
            _strip_accents(viewer_value).strip(),   # de-accented, spaces preserved
            viewer_value.strip(),                    # raw as-typed
        ):
            if candidate and candidate not in seen:
                seen.add(candidate)
                order.append(candidate)
        return order


__all__ = ["PeopleViewerResolver"]
