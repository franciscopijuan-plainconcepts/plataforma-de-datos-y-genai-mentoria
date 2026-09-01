"""Shared read-only SQL validator used by query-executing domains."""

from __future__ import annotations

import re

from src.contracts.data_access import TableDef
from src.contracts.text_to_sql import ValidationResult


_FORBIDDEN_KEYWORDS: set[str] = {
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "copy", "pg_sleep", "vacuum", "commit", "rollback",
    "merge", "replace", "into", "set", "execute", "explain", "analyze",
    "with",
}

_COMMENT_PATTERNS: list[str] = ["--", "/*", "*/"]


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip())


def _extract_identifiers(sql: str) -> set[str]:
    ids: set[str] = set()
    quoted: list[str] = re.findall(r'"([^"]+)"', sql)
    for value in quoted:
        ids.add(value.lower())
    sql_no_quoted = re.sub(r'"[^"]+"', ' ', sql)
    for match in re.finditer(r"\b([a-z_][a-z0-9_]*)\b", sql_no_quoted.lower()):
        ids.add(match.group(1))
    return ids


def validate_sql(sql: str, table_def: TableDef) -> ValidationResult:
    normalized = _normalize_sql(sql)
    if not normalized:
        return ValidationResult(accepted=False, reason="SQL is empty", sql=sql)
    if not normalized.lower().startswith("select"):
        return ValidationResult(
            accepted=False,
            reason="SQL must start with SELECT (non-SELECT statements are rejected)",
            sql=sql,
        )

    sql_lower = normalized.lower()
    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", sql_lower):
            return ValidationResult(
                accepted=False,
                reason=f"SQL contains forbidden keyword: {keyword.upper()}",
                sql=sql,
            )

    for pattern in _COMMENT_PATTERNS:
        if pattern in sql_lower:
            return ValidationResult(
                accepted=False,
                reason=f"SQL contains comment pattern: {pattern} (rejected to prevent hidden clauses)",
                sql=sql,
            )

    stripped = normalized.rstrip()
    if ";" in stripped:
        inner = stripped[:-1] if stripped.endswith(";") else stripped
        if ";" in inner:
            return ValidationResult(
                accepted=False,
                reason="SQL contains multiple statements (semicolons are not allowed except at the end)",
                sql=sql,
            )

    table_refs: set[str] = set()
    table_aliases: set[str] = set()
    for match in re.finditer(
        r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:as\s+)?([a-zA-Z_][a-zA-Z0-9_]*)?",
        sql_lower,
    ):
        table_refs.add(match.group(1))
        if match.group(2) is not None and match.group(2) not in {
            "where", "group", "order", "limit", "having", "join", "on",
            "inner", "left", "right", "outer", "full", "as",
        }:
            table_aliases.add(match.group(2))

    column_aliases: set[str] = set()
    for match in re.finditer(r"\bas\s+([a-z_][a-z0-9_]*)", sql_lower):
        column_aliases.add(match.group(1))
    # Also accept QUOTED aliases, e.g. `SUM("Sales") AS "gross_sales"` — the
    # LLM sometimes quotes them and they were being rejected as unknown columns.
    for match in re.finditer(r'\bas\s+"([^"]+)"', sql_lower):
        column_aliases.add(match.group(1))

    for ref in table_refs:
        if ref == "people":
            return ValidationResult(
                accepted=False,
                reason=(
                    "SQL references out-of-scope table: People. People is the governance mapping "
                    "table (viewer→regions) and is NOT a query surface for Text-to-SQL. "
                    "Only Orders (and Returns for net-sales metrics) may be queried."
                ),
                sql=sql,
            )

    allowed_tables = {table_def.name.lower(), "returns"}
    for ref in table_refs:
        if ref not in allowed_tables:
            return ValidationResult(
                accepted=False,
                reason=f"SQL references table: {ref}, but only '{table_def.name}' or 'Returns' is allowed",
                sql=sql,
            )

    allowed_columns = {column.name.lower() for column in table_def.columns}
    sql_keywords = {
        "select", "from", "where", "group", "by", "order", "limit", "having",
        "as", "and", "or", "not", "in", "is", "null", "like", "between",
        "asc", "desc", "offset", "distinct", "case", "when", "then", "else",
        "end", "join", "on", "inner", "left", "right", "outer", "full",
        "sum", "count", "avg", "min", "max", "now", "date", "extract",
        "year", "month", "day", "cast", "coalesce", "true", "false", "exists",
        # PostgreSQL date/time/text functions that the LLM may generate
        # (restored from main d1dd4e7 — lost in the PR #4 merge resolution):
        "to_char", "to_date", "to_timestamp", "to_number",
        "date_trunc", "date_part", "date_add", "date_sub",
        "make_date", "make_interval", "age", "interval",
        "current_date", "current_timestamp", "current_time",
        "trunc", "round", "ceil", "floor", "abs", "sqrt", "power",
        "lower", "upper", "length", "substring", "substr", "trim",
        "concat", "replace", "position", "left", "right",
        "greatest", "least", "nullif",
        "string_agg", "array_agg", "bool_or", "bool_and",
        "stddev", "variance", "median", "percentile_cont", "percentile_disc",
        "row_number", "rank", "dense_rank", "over", "partition",
    }
    target_lower = table_def.name.lower()
    identifiers = _extract_identifiers(normalized)
    for ident in identifiers:
        if ident in sql_keywords or ident == target_lower or ident in allowed_columns:
            continue
        if ident in table_aliases or ident in column_aliases or ident in allowed_tables:
            continue
        return ValidationResult(
            accepted=False,
            reason=f"SQL references non-existent column: {ident} (allowed: {sorted(allowed_columns)})",
            sql=sql,
        )

    return ValidationResult(accepted=True, reason=None, sql=sql)


__all__ = ["validate_sql"]
