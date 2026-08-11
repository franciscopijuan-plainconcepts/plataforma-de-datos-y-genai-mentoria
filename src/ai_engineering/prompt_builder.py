"""Prompt builder for Text-to-SQL (v1.0).

Builds the LLM prompt from the `DataDictionaryDocument` + the NL question.
The ONLY module that serializes `DataDictionaryDocument` into prompt format.
Does NOT call the LLM and does NOT import `openai` — pure transformation.

Reference: specs/002-text-to-sql-v1/research.md Part B
            specs/002-text-to-sql-v1/contracts/text_to_sql.md
"""

from __future__ import annotations

from src.contracts.data_access import TableDef
from src.contracts.dictionary import DataDictionaryDocument
from src.contracts.text_to_sql import NLQuestion

_SYSTEM_INSTRUCTION = (
    "You are a data analyst assistant. Translate the user's natural-language "
    "question into a single SQL SELECT query against the Orders table."
)
_SCHEMA_HEADER = "Database schema (Orders table — Transactional Logs):"
_RULES = (
    "Rules:\n"
    "- Output ONLY a single SQL SELECT statement. No explanations, no markdown.\n"
    "- Query ONLY the Orders table. Do not reference Returns or People.\n"
    "- Use only the columns listed above."
)


def build_prompt(
    question: NLQuestion, dictionary: DataDictionaryDocument, table_def: TableDef
) -> str:
    """Build the LLM prompt from the dictionary + NL question.

    Serializes the `DataDictionaryDocument` into a condensed column-table
    format (~500-800 tokens): column_name | type | nullable | key | description,
    one line per column, plus relationships and data-quality notes.
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
    prompt_parts.append("")
    prompt_parts.append(_RULES)
    prompt_parts.append("")
    prompt_parts.append(f"User question: {question.text}")

    return "\n".join(prompt_parts)


__all__ = ["build_prompt"]
