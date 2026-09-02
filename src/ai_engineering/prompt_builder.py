"""Prompt builder for Text-to-SQL (v1.0 + v2.0 semantic layer enrichment).

Builds the LLM prompt from the `DataDictionaryDocument` + the NL question.
The ONLY module that serializes `DataDictionaryDocument` into prompt format.
Does NOT call the LLM and does NOT import `openai` — pure transformation.

v2.0 (feature 003-semantic-layer-v1): optionally accepts a
`SemanticLayerDocument`. When provided, inserts a condensed metrics +
dimensions + joins block (~+400 tokens over the v1.x baseline). The LLM
sees that `net_sales` exists and how to JOIN Returns for returned-line
detection, so it can distinguish gross vs net business questions.

When `semantic_layer is None`, behavior is byte-identical to the v1.x
baseline (FR-016 fallback) — the existing Text-to-SQL pipeline tests of
feature `002-text-to-sql-v1` continue to pass unmodified.

Reference: specs/002-text-to-sql-v1/research.md Part B (baseline prompt)
            specs/003-semantic-layer-v1/research.md Part B (semantic enrichment)
            specs/003-semantic-layer-v1/contracts/integration.md (US3)
"""

from __future__ import annotations

from src.contracts.data_access import TableDef
from src.contracts.dictionary import DataDictionaryDocument
from src.contracts.semantic_layer import SemanticLayerDocument
from src.contracts.text_to_sql import NLQuestion

_SYSTEM_INSTRUCTION = (
    "You are a data analyst assistant. Translate the user's natural-language "
    "question into a single SQL SELECT query against the Orders table."
)
_SCHEMA_HEADER = "Database schema (Orders table — Transactional Logs):"

# v1.x baseline rules. Note: in v2.0 the rule that forbids Returns references
# is relaxed — see _RULES_V2 below. When the SemanticLayerDocument is provided,
# we use the v2 rule set (allows Returns JOIN for net-sales-style queries).
_RULES_V1 = (
    "Rules:\n"
    "- Output ONLY a single SQL SELECT statement. No explanations, no markdown.\n"
    "- Query ONLY the Orders table. Do not reference Returns or People.\n"
    '- ALWAYS quote table and column names with double quotes (e.g., "Sales", '
    '"Orders", "Order ID"). PostgreSQL is case-sensitive — unquoted identifiers '
    "are folded to lowercase, which will not match.\n"
    "- Use only the columns listed above."
)
_RULES_V2 = (
    "Rules:\n"
    "- Output ONLY a single SQL SELECT statement. No explanations, no markdown.\n"
    "- Query the Orders table. You MAY JOIN Returns when a metric needs returned-line "
    "detection (e.g., net sales). Do NOT reference People.\n"
    '- ALWAYS quote table and column names with double quotes (e.g., "Sales", '
    '"Orders", "Order ID"). PostgreSQL is case-sensitive — unquoted identifiers '
    "are folded to lowercase, which will not match.\n"
    "- Use only the columns listed above (or the Returns columns documented in the "
    "semantic layer section below)."
)


def build_prompt(
    question: NLQuestion,
    dictionary: DataDictionaryDocument,
    table_def: TableDef,
    semantic_layer: SemanticLayerDocument | None = None,
    extra_tables: dict[str, TableDef] | None = None,
) -> str:
    """Build the LLM prompt from the dictionary + (optional) semantic layer + NL question.

    Serializes the `DataDictionaryDocument` into a condensed column-table
    format (~500-800 tokens): column_name | type | nullable | key | description,
    one line per column, plus relationships and data-quality notes.

    v2.0: when `semantic_layer` is provided, inserts a condensed block of
    metrics + dimensions + joins between the Relationships block and the Rules
    block (~+400 tokens). See `research.md Part B` for the exact format.

    v3.2 (004-sales-prediction-model amendment): when `extra_tables` is
    provided (e.g. `{"predictions": predictions_table_def()}`), each table's
    schema is rendered as its own standalone block (Postgres-only tables like
    `Predictions` are not sourced from the Excel dictionary, so their columns
    come straight from the injected `TableDef`). The LLM is told it MAY query
    these tables independently of Orders (own `FROM` clause), for questions
    about forecasted/future sales.
    """
    # Find the Orders table dictionary entry.
    orders_dict = None
    for td in dictionary.tables:
        if td.name.lower() == "orders":
            orders_dict = td
            break
    if orders_dict is None:
        # Fallback: use the table_def columns directly.
        col_lines = [
            f"- {c.name}: {c.logical_type.value}, "
            f"{'NULL' if c.nullable else 'NOT NULL'}"
            + (", PRIMARY KEY" if c.is_primary_key else "")
            for c in table_def.columns
        ]
    else:
        col_lines = []
        for entry in orders_dict.columns:
            key_flag = ""
            if entry.is_key:
                key_flag = f", {entry.key_kind.upper() if entry.key_kind else 'KEY'}"
            nullable = "NULL" if entry.nullable else "NOT NULL"
            desc = entry.business_description[:80]
            col_lines.append(
                f"- {entry.name}: {entry.postgres_type}, {nullable}{key_flag}. {desc}"
            )

    # Relationships (compact, one line each).
    rel_lines: list[str] = []
    if orders_dict is not None:
        for rel in orders_dict.relationships:
            rel_lines.append(
                f"- {rel.from_column} -> {rel.to_table}.{rel.to_column} "
                f"({rel.cardinality}; out of scope for this query)"
            )

    # Data-quality notes (only for columns that have them).
    dq_lines: list[str] = []
    if orders_dict is not None:
        for entry in orders_dict.columns:
            for note in entry.data_quality_notes:
                dq_lines.append(f"- {entry.name}: {note}")

    prompt_parts: list[str] = [
        _SYSTEM_INSTRUCTION,
        "",
        _SCHEMA_HEADER,
    ]
    prompt_parts.extend(col_lines)
    if rel_lines:
        prompt_parts.append("")
        prompt_parts.append("Relationships:")
        prompt_parts.extend(rel_lines)
    if dq_lines:
        prompt_parts.append("")
        prompt_parts.append("Data-quality notes:")
        prompt_parts.extend(dq_lines)

    # v2.0: semantic layer block (~+400 tokens; see research.md Part B).
    if semantic_layer is not None:
        semantic_block = _render_semantic_block(semantic_layer)
        if semantic_block:
            prompt_parts.append("")
            prompt_parts.append(_render_semantic_block(semantic_layer))

    # v3.2: standalone schema block(s) for extra queryable tables (e.g.
    # Predictions). These are independent FROM-clause targets, not JOINed
    # with Orders — see the appended rule below.
    if extra_tables:
        for extra_def in extra_tables.values():
            prompt_parts.append("")
            prompt_parts.append(
                f'Database schema ("{extra_def.name}" table — {extra_def.description}):'
            )
            for c in extra_def.columns:
                key_flag = ", PRIMARY KEY" if c.is_primary_key else ""
                nullable = "NULL" if c.nullable else "NOT NULL"
                prompt_parts.append(
                    f"- {c.name}: {c.logical_type.value}, {nullable}{key_flag}"
                )

    prompt_parts.append("")
    rules = _RULES_V2 if semantic_layer is not None else _RULES_V1
    if extra_tables:
        table_names = ", ".join(f'"{td.name}"' for td in extra_tables.values())
        rules = (
            f"{rules}\n"
            f"- You MAY query {table_names} on its own (its own FROM clause) when the "
            "question is about forecasted/predicted future sales rather than historical "
            f"actuals. Use only the columns listed in the {table_names} schema block "
            "above when querying it.\n"
            f"- If the question asks to COMPARE actuals (Orders) vs forecasts "
            f"({table_names}) — e.g. by Region or Category — aggregate each table "
            "separately (each with its own GROUP BY) in its own CTE or subquery, then "
            "JOIN/UNION the two aggregates by their shared dimension (e.g. Region). "
            "Do NOT join Orders and Predictions row-by-row — Predictions rows are "
            "representative forecast profiles, not one row per historical order."
        )
    prompt_parts.append(rules)
    prompt_parts.append("")
    prompt_parts.append(f"User question: {question.text}")

    return "\n".join(prompt_parts)


def _render_semantic_block(layer: SemanticLayerDocument) -> str:
    """Render the semantic layer as a condensed block (~+400 tokens).

    Format:
      Semantic Layer (business metrics available):
      - name = formula_sql (one line per metric; description appended if short).
      ...
      Dimensions available for GROUP BY / filter:
      - dimension_name (column_name) [\n  for categorical|temporal|geographic]
      Joins available when a metric needs them:
      - Returns by Order ID (LEFT JOIN; Order ID has duplicates -> use EXISTS).
    """
    lines: list[str] = []
    lines.append("Semantic Layer (business metrics available):")
    for m in layer.metrics:
        # Condensed: name = formula_sql — description
        desc = m.business_description[:90]
        # Show the formula but cap it to keep the prompt bounded.
        formula = m.formula_sql
        if len(formula) > 200:
            formula = formula[:197] + "..."
        lines.append(f"- {m.name} = {formula} — {desc}")
        if m.uses_returns:
            lines.append(
                f"    (This metric uses Returns via ORDER_ID EXISTS; see joins below.)"
            )

    # Dimensions grouped by type.
    lines.append("")
    lines.append("Dimensions (allowed for GROUP BY / WHERE):")
    by_type: dict[str, list[str]] = {"geographic": [], "categorical": [], "temporal": []}
    for d in layer.dimensions:
        by_type.setdefault(d.dimension_type, []).append(d.column)
    for d_type, cols in by_type.items():
        if cols:
            lines.append(f"  {d_type}: {', '.join(repr(c) for c in cols)}")

    # Joins block — only show joins that reference Returns (since People is out
    # of scope for the LLM per the spec).
    lines.append("")
    lines.append("Joins available when a metric needs Returns:")
    for r in layer.relationships:
        if "Returns" not in (r.from_table, r.to_table):
            continue
        notes = r.notes or ""
        lines.append(
            f"- Returns: Orders.{r.from_column if r.from_table == 'Returns' else r.to_column} "
            f"= Returns.{r.to_column if r.to_table == 'Returns' else r.from_column} "
            f"({r.join_type} JOIN; {notes})"
        )
    return "\n".join(lines)


__all__ = ["build_prompt"]
