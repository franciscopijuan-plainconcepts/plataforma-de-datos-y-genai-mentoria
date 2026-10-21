"""Conservative PostgreSQL SELECT validation shared by query-executing domains."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.optimizer.scope import Scope, traverse_scope

from src.contracts.data_access import TableDef
from src.contracts.text_to_sql import ValidationResult


_FUNCTIONS = {
    "SUM", "COUNT", "AVG", "MIN", "MAX", "CURRENT_TIMESTAMP", "CURRENT_DATE",
    "CURRENT_TIME", "EXTRACT", "CAST", "TRY_CAST", "COALESCE", "NULLIF",
    "TO_CHAR", "TIME_TO_STR", "TO_DATE", "STR_TO_DATE", "TO_TIMESTAMP",
    "STR_TO_TIME", "UNIX_TO_TIME", "TO_NUMBER", "DATE_TRUNC", "TIMESTAMP_TRUNC",
    "DATE_PART", "DATE_ADD", "DATE_SUB", "TS_OR_DS_ADD", "TS_OR_DS_TO_DATE",
    "MAKE_DATE", "DATE_FROM_PARTS", "MAKE_INTERVAL", "AGE", "TRUNC", "ROUND",
    "CEIL", "FLOOR", "ABS", "SQRT", "POWER", "LOWER", "UPPER", "LENGTH",
    "SUBSTRING", "TRIM", "CONCAT", "REPLACE", "STR_POSITION", "LEFT", "RIGHT",
    "GREATEST", "LEAST", "GROUP_CONCAT", "ARRAY_AGG", "LOGICAL_OR",
    "LOGICAL_AND", "STDDEV", "VARIANCE", "MEDIAN", "PERCENTILE_CONT",
    "PERCENTILE_DISC", "ROW_NUMBER", "RANK", "DENSE_RANK", "IF", "CASE",
    "AND", "OR", "EXISTS",
}
_RETURNS_COLUMNS = {"Return ID", "Order ID", "Returned", "Region"}


Schemas = dict[str, set[str]]


def _schemas(table_def: TableDef, extra_tables: dict[str, TableDef] | None) -> Schemas:
    """Lower-cased table name -> column names for every queryable table."""
    schemas: Schemas = {"returns": set(_RETURNS_COLUMNS)}
    for definition in [*(extra_tables or {}).values(), table_def]:
        schemas[definition.name.lower()] = {column.name for column in definition.columns}
    return schemas


def _source_columns(source: exp.Expression | Scope, schemas: Schemas) -> set[str]:
    if isinstance(source, exp.Table):
        return schemas.get(source.name.lower(), set())
    if isinstance(source, Scope):
        # Only our filtered single-table SELECT * sources may be admitted.
        return set().union(*(_source_columns(child, schemas) for child in source.sources.values()))
    return set()


def _column_error(scope: Scope, column: exp.Column, schemas: Schemas) -> str | None:
    cursor: Scope | None = scope
    while cursor is not None:
        sources = cursor.sources
        if column.table:
            matches = [source for alias, source in sources.items()
                       if alias.lower() == column.table.lower()]
            if matches:
                if isinstance(column.this, exp.Star):
                    return None
                available = _source_columns(matches[0], schemas)
                if column.name.lower() in {name.lower() for name in available}:
                    return None
                return f"SQL references non-existent column: {column.sql()}"
        else:
            matches = [source for source in sources.values()
                       if column.name.lower() in {name.lower() for name in _source_columns(source, schemas)}]
            if len(matches) == 1:
                return None
            if len(matches) > 1:
                return f"SQL references ambiguous column: {column.name}"
            if (cursor is scope and isinstance(scope.expression, exp.Select)
                    and column.name in scope.expression.named_selects):
                # Output aliases are meaningful only in GROUP BY / ORDER BY.
                ancestor = column.parent
                while ancestor is not None and ancestor is not scope.expression:
                    if isinstance(ancestor, (exp.Order, exp.Group)):
                        return None
                    ancestor = ancestor.parent
        cursor = cursor.parent if cursor.can_be_correlated else None
    return f"SQL references non-existent column or qualifier: {column.sql()}"


def _error(sql: str, allow_governed_sources: bool, schemas: Schemas) -> str | None:
    if not sql.strip():
        return "SQL is empty"
    if len(sql.encode("utf-8")) > 200_000:
        return "SQL exceeds the supported size"
    try:
        tokens = sqlglot.tokenize(sql, read="postgres")
        if any(token.comments for token in tokens):
            return "SQL contains a comment (rejected to prevent hidden clauses)"
        statements = sqlglot.parse(sql, read="postgres")
    except (ParseError, TokenError, RecursionError) as exc:
        return f"SQL parsing failed: {exc}"
    if len(statements) != 1:
        return "SQL contains multiple statements"
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        return "SQL must start with SELECT; set operations are not supported"
    if tree.find(exp.With, exp.Into, exp.Lock, exp.SetOperation, exp.Command):
        return "SQL contains a forbidden or unsupported statement"
    for node in tree.walk():
        if isinstance(node, (exp.DML, exp.DDL)):
            return "SQL contains a forbidden data-changing statement"
        if node.comments:
            return "SQL contains a comment"
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
            if name not in _FUNCTIONS:
                return f"SQL contains forbidden or unsupported function: {name}"
        if isinstance(node, exp.Dot):
            return "Schema-qualified functions and expressions are unsupported"
    for table in tree.find_all(exp.Table):
        if table.name.lower() == "people":
            return "SQL references out-of-scope table: People (governance mapping)"
        if (table.name.lower() not in schemas
                or table.db or table.catalog or not isinstance(table.this, exp.Identifier)
                or any(value for key, value in table.args.items() if key not in {"this", "alias"})
                or (table.args.get("alias") is not None and table.args["alias"].args.get("columns"))):
            return f"SQL references unsupported table or source: {table.sql()}"
    for scope in traverse_scope(tree):
        if not isinstance(scope.expression, exp.Select):
            return "Only SELECT scopes are supported"
        aliases = [table.alias_or_name.lower() for table in scope.tables]
        if len(aliases) != len(set(aliases)):
            return "SQL contains duplicate table aliases"
        if scope.expression.args.get("with_") or scope.expression.args.get("into") or scope.expression.args.get("locks"):
            return "SQL contains a forbidden clause"
        for source in scope.sources.values():
            if not isinstance(source, (Scope, exp.Table)):
                return "Unsupported SQL source"
            if isinstance(source, Scope):
                if not allow_governed_sources:
                    return "Derived table sources are unsupported in generated SQL"
                inner = source.expression
                empty_wrapper = (
                    scope.expression is tree and len(tree.expressions) == 1
                    and isinstance(tree.expressions[0], exp.Star)
                    and tree.args.get("where") == exp.Where(this=exp.false())
                    and not any(value for key, value in tree.args.items()
                                if key not in {"expressions", "from_", "where"})
                )
                if not empty_wrapper and (
                        not isinstance(inner, exp.Select) or len(source.tables) != 1
                        or len(inner.expressions) != 1 or not isinstance(inner.expressions[0], exp.Star)
                        or any(value for key, value in inner.args.items()
                               if key not in {"expressions", "from_", "where"})):
                    return "Unsupported governed source"
        for column in [*scope.columns, *scope.stars]:
            if not isinstance(column, exp.Column):
                return "Unsupported qualified star expression"
            reason = _column_error(scope, column, schemas)
            if reason is not None:
                return reason
    return None


def validate_sql(
    sql: str, table_def: TableDef, *, allow_governed_sources: bool = False,
    extra_tables: dict[str, TableDef] | None = None,
) -> ValidationResult:
    """Validate one read-only SELECT against `table_def`, Returns and `extra_tables`.

    `extra_tables` (004-sales-prediction-model) admits additional tables, with
    their own columns, that generated SQL may query, e.g. Predictions.
    """
    schemas = _schemas(table_def, extra_tables)
    reason = _error(sql, allow_governed_sources, schemas)
    return ValidationResult(accepted=reason is None, reason=reason, sql=sql)


__all__ = ["validate_sql"]
