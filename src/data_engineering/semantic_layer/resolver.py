"""Pure AST transformation restricting each analytic scan before aggregation."""

from __future__ import annotations

import logging
from typing import Callable

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from src.contracts.data_access import TableDef
from src.contracts.semantic_layer import SemanticViewer
from src.data_access.sql_validator import validate_sql


_logger = logging.getLogger(__name__)
GovBypassLogger = Callable[[str, str, list[str]], None]


def _default_gov_bypass_logger(viewer_id: str, sql: str, regions: list[str]) -> None:
    _logger.warning("gov.bypass: viewer=%s regions=%s sql=%r", viewer_id, regions, sql)


def _column_source(scope: Scope, column: exp.Column) -> exp.Table | None:
    if not column.table:
        return None
    cursor: Scope | None = scope
    while cursor is not None:
        source = cursor.sources.get(column.table)
        if source is not None:
            return source if isinstance(source, exp.Table) else None
        cursor = cursor.parent if cursor.can_be_correlated else None
    return None


def _conjuncts(expression: exp.Expr) -> list[exp.Expr]:
    expression = expression.unnest()
    if isinstance(expression, exp.And):
        return _conjuncts(expression.this) + _conjuncts(expression.expression)
    return [expression]


def _order_linked_returns(tree: exp.Select) -> set[int]:
    """Keep legacy global return existence only behind an authorized order key."""
    linked: set[int] = set()
    for scope in traverse_scope(tree):
        returns = [table for table in scope.tables if table.name.lower() == "returns"]
        if not returns or scope.expression is tree:
            continue
        select = scope.expression
        valid_exists = (
            isinstance(select, exp.Select) and isinstance(select.parent, exp.Exists)
            and len(scope.sources) == 1 and len(returns) == 1
            and len(select.expressions) == 1
            and isinstance(select.expressions[0], exp.Literal)
            and select.expressions[0].this == "1"
            and not select.expressions[0].is_string
            and select.args.get("where") is not None
        )
        if valid_exists:
            for predicate in _conjuncts(select.args["where"].this):
                if not isinstance(predicate, exp.EQ):
                    continue
                left, right = predicate.this, predicate.expression
                if (not isinstance(left, exp.Column) or not isinstance(right, exp.Column)
                        or left.name != "Order ID" or right.name != "Order ID"):
                    continue
                sources = [_column_source(scope, left), _column_source(scope, right)]
                if (any(source is returns[0] for source in sources)
                        and any(source is not None and source not in scope.tables
                                and source.name.lower() == "orders" for source in sources)):
                    linked.add(id(returns[0]))
                    break
        if any(id(table) not in linked for table in returns):
            raise ValueError(
                "Returns subquery must be SELECT 1 inside EXISTS with a mandatory "
                "Order ID equality to an outer authorized Orders scan"
            )
    return linked


class SemanticQueryResolver:
    def __init__(self, gov_bypass_logger: GovBypassLogger | None = None) -> None:
        self._gov_bypass_logger = gov_bypass_logger or _default_gov_bypass_logger

    def apply_rls(
        self, sql: str, viewer: SemanticViewer, table_def: TableDef,
        extra_tables: dict[str, TableDef] | None = None,
    ) -> str:
        validation = validate_sql(sql, table_def, extra_tables=extra_tables)
        if not validation.accepted:
            raise ValueError(f"Governance rejected SQL: {validation.reason}")
        if viewer.allows_full_access and viewer.is_local_dev:
            self._gov_bypass_logger(viewer.viewer_id, sql, list(viewer.regions))
            return sql
        refused_bypass = viewer.allows_full_access and not viewer.is_local_dev
        if refused_bypass:
            _logger.warning("gov.bypass refused in non-local environment: viewer=%s", viewer.viewer_id)
        no_access = refused_bypass or not viewer.regions
        tree = sqlglot.parse_one(sql, read="postgres")
        if not isinstance(tree, exp.Select):
            raise ValueError("Governance requires a SELECT")
        linked_returns = _order_linked_returns(tree)
        # Every extra table (e.g. Predictions) carries a Region column and is
        # filtered exactly like the primary table; anything else is Returns.
        physical_names = {definition.name.lower(): definition.name
                          for definition in [*(extra_tables or {}).values(), table_def]}
        # Snapshot the original scans: never visit the replacements recursively.
        for table in list(tree.find_all(exp.Table)):
            if id(table) in linked_returns:
                if table.args.get("alias") is None:
                    table.set("alias", exp.TableAlias(this=table.this.copy()))
                table.set("this", exp.to_identifier("Returns", quoted=True))
                continue
            alias = table.args.get("alias")
            if alias is None:
                alias = exp.TableAlias(this=table.this.copy())
            physical_name = physical_names.get(table.name.lower(), "Returns")
            physical = exp.Table(this=exp.to_identifier(physical_name, quoted=True))
            predicate = (
                exp.false() if no_access else exp.In(
                    this=exp.column("Region", quoted=True),
                    expressions=[exp.Literal.string(region) for region in viewer.regions],
                )
            )
            filtered = exp.select("*").from_(physical).where(predicate)
            table.replace(exp.Subquery(this=filtered, alias=alias.copy()))
        if no_access:
            # Preserve the public no-region behavior: no aggregate row either.
            tree = exp.select("*").from_(tree.subquery("_gov")).where(exp.false())
        governed = tree.sql(dialect="postgres")
        validation = validate_sql(governed, table_def, allow_governed_sources=True,
                                  extra_tables=extra_tables)
        if not validation.accepted:
            raise ValueError(f"Governed SQL failed validation: {validation.reason}")
        return governed
