"""SQL validator for LLM-generated SQL (v1.0).

Validates that the generated SQL is a single SELECT statement against the
`Orders` table only, referencing only existing columns. Pure function with
no LLM or DB dependencies — fully unit-testable.

Reference: specs/002-text-to-sql-v1/research.md Part A
            specs/002-text-to-sql-v1/contracts/text_to_sql.md
"""

from __future__ import annotations

import re

from src.contracts.data_access import TableDef
from src.contracts.text_to_sql import ValidationResult


# Keywords that MUST NEVER appear in a read-only query.
_FORBIDDEN_KEYWORDS: set[str] = {
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "copy", "pg_sleep", "vacuum", "commit", "rollback",
    "merge", "replace", "into", "set", "execute", "explain", "analyze",
    "with",  # CTEs are out of scope for v1.0 — keep it simple
}

# Comment patterns used to hide malicious clauses.
_COMMENT_PATTERNS: list[str] = ["--", "/*", "*/"]


def _normalize_sql(sql: str) -> str:
    """Strip leading/trailing whitespace and normalize internal whitespace."""
    return re.sub(r"\s+", " ", sql.strip())


def _extract_identifiers(sql: str) -> set[str]:
    """Extract identifier-like tokens from the SQL (column/table names).

    First removes quoted identifiers ("Col Name") so their internal words
    don't appear as bare identifiers. Then matches quoted identifiers again
    for collection, and bare identifiers (word sequences after FROM, SELECT,
    WHERE, etc.). Returns a set of lowercased names.
    """
    ids: set[str] = set()
    # Collect quoted identifiers first: "Order ID" -> order id
    quoted: list[str] = re.findall(r'"([^"]+)"', sql)
    for q in quoted:
        ids.add(q.lower())
    # Remove quoted identifiers from the SQL so their internal words
    # (e.g., "ID" in "Order ID") don't appear as bare identifiers.
    sql_no_quoted = re.sub(r'"[^"]+"', " ", sql)
    # Bare identifiers: word sequences after removing quoted strings.
    for m in re.finditer(r"\b([a-z_][a-z0-9_]*)\b", sql_no_quoted.lower()):
        ids.add(m.group(1))
    return ids


def validate_sql(sql: str, table_def: TableDef) -> ValidationResult:
    """Validate LLM-generated SQL before execution.

    Checks (in order):
    1. Non-empty and starts with SELECT (after stripping).
    2. No forbidden keywords (INSERT, UPDATE, DROP, etc.).
    3. No comments (-- or /*).
    4. Single statement (no semicolons except optionally at the very end).
    5. References only the `table_def.name` table (reject other tables).
    6. References only columns that exist in `table_def.columns`.

    Returns a `ValidationResult` (accepted=True or accepted=False + reason).
    """
    normalized = _normalize_sql(sql)

    # 1. Must be non-empty and start with SELECT.
    if not normalized:
        return ValidationResult(accepted=False, reason="SQL is empty", sql=sql)
    if not normalized.lower().startswith("select"):
        return ValidationResult(
            accepted=False,
            reason="SQL must start with SELECT (non-SELECT statements are rejected)",
            sql=sql,
        )

    # 2. Check for forbidden keywords.
    sql_lower = normalized.lower()
    for kw in _FORBIDDEN_KEYWORDS:
        # Word-boundary match so "delete" doesn't match "deleted".
        if re.search(rf"\b{re.escape(kw)}\b", sql_lower):
            return ValidationResult(
                accepted=False,
                reason=f"SQL contains forbidden keyword: {kw.upper()}",
                sql=sql,
            )

    # 3. Check for comment patterns.
    for pattern in _COMMENT_PATTERNS:
        if pattern in sql_lower:
            return ValidationResult(
                accepted=False,
                reason=f"SQL contains comment pattern: {pattern} (rejected to prevent hidden clauses)",
                sql=sql,
            )

    # 4. Single statement: reject if semicolons appear before the end.
    stripped = normalized.rstrip()
    if ";" in stripped:
        # Allow a trailing semicolon, reject anything else.
        inner = stripped[:-1] if stripped.endswith(";") else stripped
        if ";" in inner:
            return ValidationResult(
                accepted=False,
                reason="SQL contains multiple statements (semicolons are not allowed except at the end)",
                sql=sql,
            )

    # 5. Table whitelist: orders + returns (v2.0 allows Returns JOIN for
    #    metric formulas like net_sales). People stays rejected — it is NOT
    #    a query surface for the LLM (used only internally for viewer->regions).
    #    Extract table names that appear after FROM/JOIN.
    table_refs: set[str] = set()
    # Also collect table aliases — e.g. "FROM Orders o" -> alias "o";
    # "JOIN Returns r" -> alias "r". These are valid references and must NOT
    # be rejected by the column whitelist (they appear in expressions like
    # o."Sales" or r."Order ID").
    table_aliases: set[str] = set()
    for m in re.finditer(
        r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:as\s+)?([a-zA-Z_][a-zA-Z0-9_]*)?",
        sql_lower,
    ):
        table_refs.add(m.group(1))
        # The optional second group is the alias (may be None if no alias).
        if m.group(2) is not None:
            # Distinguish alias from a following keyword (e.g., "FROM Orders WHERE").
            # If the second token is a SQL keyword, it's NOT an alias.
            if m.group(2) not in {
                "where", "group", "order", "limit", "having", "join", "on",
                "inner", "left", "right", "outer", "full", "as",
            }:
                table_aliases.add(m.group(2))
    # v2.0: also collect column aliases introduced via `AS` (e.g.,
    # `SUM(o."Sales") AS net` -> alias "net"). These are not columns and
    # must not be rejected by the column whitelist.
    column_aliases: set[str] = set()
    for m in re.finditer(r"\bas\s+([a-z_][a-z0-9_]*)", sql_lower):
        column_aliases.add(m.group(1))
    # Reject People references explicitly.
    known_other_tables = {"people"}
    for ref in table_refs:
        if ref in known_other_tables:
            return ValidationResult(
                accepted=False,
                reason=(
                    f"SQL references out-of-scope table: People. People is the "
                    "governance mapping table (viewer→regions) and is NOT a query "
                    "surface for Text-to-SQL. Only Orders (and Returns for net-sales "
                    "metrics) may be queried."
                ),
                sql=sql,
            )
    # If specific table refs were found, they must all match the target table
    # or the Returns table (v2.0 allows Returns JOIN for metric formulas).
    allowed_tables = {table_def.name.lower(), "returns"}
    for ref in table_refs:
        if ref not in allowed_tables:
            return ValidationResult(
                accepted=False,
                reason=f"SQL references table: {ref}, but only '{table_def.name}' or 'Returns' is allowed",
                sql=sql,
            )

    # 6. Column whitelist: every referenced column must exist.
    target_lower = table_def.name.lower()
    allowed_columns = {c.name.lower() for c in table_def.columns}
    # SQL keywords/functions that are NOT columns (don't reject these).
    sql_keywords = {
        "select", "from", "where", "group", "by", "order", "limit", "having",
        "as", "and", "or", "not", "in", "is", "null", "like", "between",
        "asc", "desc", "offset", "distinct", "case", "when", "then", "else",
        "end", "join", "on", "inner", "left", "right", "outer", "full",
        "sum", "count", "avg", "min", "max", "now", "date", "extract",
        "year", "month", "day", "cast", "coalesce", "true", "false",
        # v2.0: EXISTS subqueries are now allowed (for Returns detection in
        #       net-sales-style metrics). Add the keyword so the column
        #       whitelist does not reject it as a non-existent column.
        "exists",
        # PostgreSQL date/time functions that the LLM may generate:
        "to_char", "to_date", "to_timestamp", "to_number",
        "date_trunc", "date_part", "date_add", "date_sub",
        "make_date", "make_interval", "age", "interval",
        "current_date", "current_timestamp", "current_time",
        "trunc", "round", "ceil", "floor", "abs", "sqrt", "power",
        "lower", "upper", "length", "substring", "substr", "trim",
        "concat", "replace", "position", "left", "right",
        "greatest", "least", "nullif", "greatest", "least",
        "string_agg", "array_agg", "bool_or", "bool_and",
        "stddev", "variance", "median", "percentile_cont", "percentile_disc",
        "row_number", "rank", "dense_rank", "over", "partition",
    }
    identifiers = _extract_identifiers(normalized)
    for ident in identifiers:
        if ident in sql_keywords:
            continue
        if ident == target_lower:
            continue
        if ident in allowed_columns:
            continue
        # v2.0: allow identifiers that are table aliases (e.g., "o" in
        # "FROM Orders o" or "r" in "JOIN Returns r"). These appear in
        # qualified references like o."Sales" but aren't columns themselves.
        if ident in table_aliases:
            continue
        # v2.0: allow column aliases introduced via AS (e.g., "net" in
        # `SUM(...) AS net`). These are output column names, not table columns.
        if ident in column_aliases:
            continue
        if ident in allowed_tables:
            continue
        # It's not a keyword, not the table name, not an alias, and not a
        # known column — reject. literals (numbers/strings) won't match the
        # identifier regex.
        return ValidationResult(
            accepted=False,
            reason=f"SQL references non-existent column: {ident} (allowed: {sorted(allowed_columns)})",
            sql=sql,
        )

    return ValidationResult(accepted=True, reason=None, sql=sql)


__all__ = ["validate_sql"]
