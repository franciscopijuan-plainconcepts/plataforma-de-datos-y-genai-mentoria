"""Governed QueryProvider decorator + fail-fast safety net (v2.0).

The `GovernedQueryProvider` wraps any `QueryProvider` and enforces Row-Level
Security on every `execute_readonly_query` call by running the input SQL
through the `SemanticQueryResolver` before delegating execution. This is the
single constitutional enforcement point for Principle IV (RLS NON-NEGOTIABLE).

The `_UngovernedFailFastProvider` is a safety net returned by the CLI
composition root when no viewer is configured — it raises `ValueError` on the
first call so the system NEVER silently executes a query without governance.

Reference: specs/003-semantic-layer-v1/contracts/integration.md (Composition root
            + Decorator pattern).
            specs/003-semantic-layer-v1/research.md Part E (Boundary enforcement).
            specs/003-semantic-layer-v1/tasks.md T016, T017, T019.
"""

from __future__ import annotations

from src.contracts.data_access import TableDef
from src.contracts.semantic_layer import (
    SemanticQueryResolverProtocol,
    SemanticViewer,
)
from src.contracts.text_to_sql import QueryRow
from src.data_access.interfaces import QueryProvider


class GovernedQueryProvider:
    """Decorator over `QueryProvider` that enforces Semantic Layer RLS.

    Implements the `QueryProvider` Protocol. Every call to
    `execute_readonly_query` is intercepted: the SQL is transformed by the
    injected `SemanticQueryResolverProtocol` (subquery wrapping with the
    `WHERE "Region" IN (...)` clause per the viewer's regions) before being
    delegated to the wrapped `QueryProvider` (e.g., `PostgresRepository`).

    The wrapped provider executes the GOVERNED SQL — it has no awareness of
    RLS. This keeps the adapter engine-agnostic (the future BigQuery adapter
    will be wrapped the same way).
    """

    def __init__(
        self,
        delegate: QueryProvider,
        resolver: SemanticQueryResolverProtocol,
        viewer: SemanticViewer,
        table_def: TableDef,
    ) -> None:
        self._delegate = delegate
        self._resolver = resolver
        self._viewer = viewer
        self._table_def = table_def

    def execute_readonly_query(
        self, sql: str, table_def: TableDef
    ) -> list[QueryRow]:
        """Apply RLS to the SQL, then delegate execution to the wrapped provider.

        The caller is the `TextToSqlPipeline`, which has ALREADY validated
        the SQL via `SqlValidator` before calling this method. The resolver
        is therefore operating on known-safe SELECT SQL.
        """
        governed_sql = self._resolver.apply_rls(sql, self._viewer, table_def)
        return self._delegate.execute_readonly_query(governed_sql, table_def)


class _UngovernedFailFastProvider:
    """Safety net that raises on any `execute_readonly_query` call.

    Returned by the CLI composition root (`build_query_provider`) when no
    viewer is configured. Prevents any silent execution path without
    governance (Constitution Principle IV — NON-NEGOTIABLE).
    """

    _MESSAGE = (
        "Governance is non-negotiable (constitution Principle IV). "
        "Provide --viewer <id> or, in local/dev, --allow-full-access."
    )

    def execute_readonly_query(
        self, sql: str, table_def: TableDef
    ) -> list[QueryRow]:
        raise ValueError(self._MESSAGE)


def build_governed_provider(
    delegate: QueryProvider,
    resolver: SemanticQueryResolverProtocol,
    viewer: SemanticViewer | None,
    table_def: TableDef,
) -> QueryProvider:
    """CLI composition-root helper: wire the right `QueryProvider` for the viewer.

    Returns:
        - `GovernedQueryProvider` wrapping `delegate` when `viewer` is provided.
        - `_UngovernedFailFastProvider` when `viewer` is None (no governance
          context — any query will fail-fast rather than bypass RLS).

    This is the ONLY constructor path the CLI uses to build a `QueryProvider`
    for `TextToSqlPipeline`; the boundary test asserts it.
    """
    if viewer is None:
        return _UngovernedFailFastProvider()
    return GovernedQueryProvider(
        delegate=delegate,
        resolver=resolver,
        viewer=viewer,
        table_def=table_def,
    )


__all__ = [
    "GovernedQueryProvider",
    "build_governed_provider",
]
