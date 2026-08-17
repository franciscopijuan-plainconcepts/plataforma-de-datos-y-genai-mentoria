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

    # 5. Table whitelist: only the target table is allowed.
    #    Extract table names that appear after FROM/JOIN.
    table_refs: set[str] = set()
    for m in re.finditer(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql_lower):
        table_refs.add(m.group(1))
    # Also check if any other known table names appear. We know the three
    # tables in the warehouse; reject if Returns or People are referenced.
    known_other_tables = {"returns", "people"}
    for ref in table_refs:
        if ref in known_other_tables:
            return ValidationResult(
                accepted=False,
                reason=f"SQL references out-of-scope table: {ref}. v1.x scope is '{table_def.name}' only (other tables are v2.0 scope)",
                sql=sql,
            )
    # If specific table refs were found, they must all match the target table.
    target_lower = table_def.name.lower()
    for ref in table_refs:
        if ref != target_lower:
            return ValidationResult(
                accepted=False,
                reason=f"SQL references table: {ref}, but only '{table_def.name}' is allowed",
                sql=sql,
            )

    # 6. Column whitelist: every referenced column must exist.
    allowed_columns = {c.name.lower() for c in table_def.columns}
    # SQL keywords/functions that are NOT columns (don't reject these).
    sql_keywords = {
        "select", "from", "where", "group", "by", "order", "limit", "having",
        "as", "and", "or", "not", "in", "is", "null", "like", "between",
        "asc", "desc", "offset", "distinct", "case", "when", "then", "else",
        "end", "join", "on", "inner", "left", "right", "outer", "full",
        "sum", "count", "avg", "min", "max", "now", "date", "extract",
        "year", "month", "day", "cast", "coalesce", "true", "false",
    }
    identifiers = _extract_identifiers(normalized)
    for ident in identifiers:
        if ident in sql_keywords:
            continue
        if ident == target_lower:
            continue
        if ident in allowed_columns:
            continue
        # It's not a keyword, not the table name, and not a known column — reject.
        # But only reject if it appears to be a column reference (not a literal).
        # literals (numbers/strings) won't match the identifier regex.
        return ValidationResult(
            accepted=False,
            reason=f"SQL references non-existent column: {ident} (allowed: {sorted(allowed_columns)})",
            sql=sql,
        )

    return ValidationResult(accepted=True, reason=None, sql=sql)


__all__ = ["validate_sql"]
