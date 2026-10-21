"""Request-scoped governed preparation, execution and QueryProvider compatibility.

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
    SemanticModelProvenance,
    SemanticViewer,
)
from src.contracts.text_to_sql import GovernedExecutionProvider, PreparedQuery, QueryRow
from src.data_access.interfaces import QueryProvider


class GovernedQueryProvider:
    """Decorator over `QueryProvider` that enforces Semantic Layer RLS.

    Implements both the legacy QueryProvider and the narrow governed boundary.
    Preparation transforms and validates once. Prepared execution delegates the
    exact captured SQL without rewriting it again.

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
        model_sha256: str | None = None,
        provenance: SemanticModelProvenance | None = None,
        extra_tables: dict[str, TableDef] | None = None,
    ) -> None:
        self._delegate = delegate
        self._resolver = resolver
        self._viewer = viewer.model_copy(deep=True)
        self._table_def = table_def.model_copy(deep=True)
        if provenance is not None and model_sha256 not in {None, provenance.model_sha256}:
            raise ValueError("Conflicting semantic model hashes")
        self._model_sha256 = provenance.model_sha256 if provenance is not None else model_sha256
        self._provenance = provenance
        self._extra_tables = {name: definition.model_copy(deep=True)
                              for name, definition in (extra_tables or {}).items()}

    def prepare_query(self, sql: str, table_def: TableDef) -> PreparedQuery:
        if table_def != self._table_def:
            raise ValueError("Query schema differs from the governed provider schema")
        governed_sql = self._resolver.apply_rls(
            sql, self._viewer, table_def, extra_tables=self._extra_tables
        )
        return PreparedQuery(generated_sql=sql, sql=governed_sql,
                             viewer=self._viewer.model_copy(deep=True),
                             model_sha256=self._model_sha256, provenance=self._provenance)

    def execute_prepared_query(self, query: PreparedQuery, table_def: TableDef) -> list[QueryRow]:
        if (query.viewer != self._viewer or query.model_sha256 != self._model_sha256
                or query.provenance != self._provenance
                or table_def != self._table_def):
            raise ValueError("Prepared query context differs from the executor context")
        return self._delegate.execute_readonly_query(query.sql, table_def)

    def execute_readonly_query(
        self, sql: str, table_def: TableDef
    ) -> list[QueryRow]:
        """Compatibility entrypoint; interactive pipelines use explicit preparation."""
        return self.execute_prepared_query(self.prepare_query(sql, table_def), table_def)


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

    def prepare_query(self, sql: str, table_def: TableDef) -> PreparedQuery:
        raise ValueError(self._MESSAGE)

    def execute_prepared_query(self, query: PreparedQuery, table_def: TableDef) -> list[QueryRow]:
        raise ValueError(self._MESSAGE)


def build_governed_provider(
    delegate: QueryProvider,
    resolver: SemanticQueryResolverProtocol,
    viewer: SemanticViewer | None,
    table_def: TableDef,
    model_sha256: str | None = None,
    provenance: SemanticModelProvenance | None = None,
    extra_tables: dict[str, TableDef] | None = None,
) -> GovernedExecutionProvider:
    """CLI composition-root helper: require a viewer for governed execution.

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
        model_sha256=model_sha256,
        provenance=provenance,
        extra_tables=extra_tables,
    )


__all__ = [
    "GovernedQueryProvider",
    "build_governed_provider",
]
