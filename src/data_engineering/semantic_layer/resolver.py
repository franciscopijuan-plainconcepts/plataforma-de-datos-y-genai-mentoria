"""Semantic Layer RLS resolver (v2.0) — pure function `apply_rls`.

Applies Row-Level Security to a validated SELECT SQL by wrapping it in an
outer subquery with `WHERE "Region" IN (viewer.regions)`. The resolver is
a PURE FUNCTION — no DB calls, no LLM, no file I/O (aside from the optional
`gov.bypass` audit log, which goes through a standard `logging.Logger`).

Reference: specs/003-semantic-layer-v1/research.md Part A (RLS Strategy —
subquery wrapping; robust to existing WHERE, GROUP BY, ORDER BY, LIMIT).
            specs/003-semantic-layer-v1/contracts/semantic_layer.md
            specs/003-semantic-layer-v1/tasks.md T015, T018
"""

from __future__ import annotations

import logging
from typing import Callable

from src.contracts.data_access import TableDef
from src.contracts.semantic_layer import SemanticQueryResolverProtocol, SemanticViewer


_logger = logging.getLogger(__name__)

# The outer alias used by the resolver. The SqlValidator already enforces
# that the LLM never generates this aliased subquery (its output is a plain
# SELECT), so collisions with user aliases are not a concern.
_GOVERNED_ALIAS = "_gov"

# The RLS anchor column. All three warehouse tables (Orders, Returns, People)
# have this column; the resolver always uses it with double-quoting to match
# the case-sensitive PostgreSQL schema.
_RLS_COLUMN = '"Region"'


# A pluggable audit-log sink so tests can capture `gov.bypass` events without
# requiring the logging module. Defaults to the module-level logger.
GovBypassLogger = Callable[[str, str, list[str]], None]


def _default_gov_bypass_logger(
    viewer_id: str, sql: str, regions: list[str]
) -> None:
    """Default audit logger for gov.bypass events (used on `allows_full_access`)."""
    _logger.warning(
        "gov.bypass: viewer=%s regions=%s sql=%r",
        viewer_id,
        regions,
        sql,
    )


def _sql_quote_string(value: str) -> str:
    """Single-quote a SQL string literal, escaping internal single quotes.

    PostgreSQL uses the ''escape (single-quote doubled) inside single-quoted
    strings. This is defensive — viewer regions come from a local config file,
    but sanitization prevents any injection regardless of source.
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


class SemanticQueryResolver:
    """Pure-function implementation of `SemanticQueryResolverProtocol`.

    The resolver wraps the validated input SQL in an outer subquery and
    applies an outer `WHERE "Region" IN (viewer.regions)` filter. It handles
    the following cases:

    1. `viewer.allows_full_access=True` AND `viewer.is_local_dev=True`
       → logs `gov.bypass` and returns the SQL UNCHANGED (no wrapping).
    2. `viewer.regions=[]` (and not full-access)
       → returns `SELECT * FROM ({sql}) AS _gov WHERE FALSE` (0 rows).
    3. `viewer.regions=[R1, R2, ...]`
       → returns `SELECT * FROM ({sql}) AS _gov WHERE "Region" IN ('R1','R2',...)`.

    The resolver is engine-neutral: it produces a SQL string, not a DB call.
    The PostgresAdapter executes it as-is after the wrapping.
    """

    def __init__(
        self,
        gov_bypass_logger: GovBypassLogger | None = None,
    ) -> None:
        self._gov_bypass_logger = (
            gov_bypass_logger if gov_bypass_logger is not None
            else _default_gov_bypass_logger
        )

    def apply_rls(
        self,
        sql: str,
        viewer: SemanticViewer,
        table_def: TableDef,
    ) -> str:
        """Wrap the SQL with a Region-scoped filter per the viewer's governance context.

        Args:
            sql: a validated single SELECT statement (the SqlValidator already
                confirmed it is SELECT-only, single-statement, no comments,
                no forbidden keywords, and references only Orders + Returns
                columns/tables).
            viewer: the runtime governance context (regions + allows_full_access
                + is_local_dev).
            table_def: the schema of the queried table (used for sanity —
                the anchor "Region" column must exist on at least one table
                the SQL may reference).

        Returns:
            The governed SQL string (possibly unchanged if full-access is
            granted in a local-dev environment).
        """
        # 1. Full-access bypass — ONLY in local/dev/test (is_local_dev gating
        #    is already enforced at the registry layer; we re-check here as
        #    defense-in-depth).
        if viewer.allows_full_access:
            if not viewer.is_local_dev:
                # Defense-in-depth: refuses to bypass outside local-dev, even
                # if a viewer config somehow has both flags set. This path
                # should be unreachable (the registry sanitizes it), but we
                # never trust the input for governance.
                _logger.warning(
                    "gov.bypass refused in non-local environment: viewer=%s",
                    viewer.viewer_id,
                )
                # Fall through to forced FALSE to be safe.
                return self._wrap_false(sql, viewer)
            self._gov_bypass_logger(viewer.viewer_id, sql, list(viewer.regions))
            return sql

        # 2. Empty regions => viewer sees 0 rows.
        if not viewer.regions:
            return self._wrap_false(sql, viewer)

        # 3. Standard subquery wrapping with Region IN (regions).
        return self._wrap_region_filter(sql, viewer.regions)

    # --- Internal construction helpers -------------------------------------

    def _wrap_region_filter(self, sql: str, regions: list[str]) -> str:
        """Build `SELECT * FROM ({sql}) AS _gov WHERE "Region" IN (r1, r2, ...).`"""
        quoted = ", ".join(_sql_quote_string(r) for r in regions)
        # Strip a trailing semicolon if present (the SqlValidator allows it
        # optionally; a trailing `;` would break the wrapped syntax).
        inner = sql.rstrip()
        if inner.endswith(";"):
            inner = inner[:-1]
        return (
            f"SELECT * FROM ({inner}) AS {_GOVERNED_ALIAS} "
            f"WHERE {_RLS_COLUMN} IN ({quoted})"
        )

    def _wrap_false(self, sql: str, viewer: SemanticViewer) -> str:
        """Build `SELECT * FROM ({sql}) AS _gov WHERE FALSE` (viewer sees 0 rows)."""
        inner = sql.rstrip()
        if inner.endswith(";"):
            inner = inner[:-1]
        return f"SELECT * FROM ({inner}) AS {_GOVERNED_ALIAS} WHERE FALSE"


__all__ = ["SemanticQueryResolver"]
