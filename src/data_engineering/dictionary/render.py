"""Markdown renderer for the `DataDictionaryDocument`.

Produces the committed `data_dictionary.md` artifact from a typed
`DataDictionaryDocument`. The renderer is a pure function (no I/O of its own);
the CLI `generate-dictionary` command writes the rendered string to disk.

Format (per contracts/dictionary.md § Output Format):
- A header with generation metadata (source file + hash + timestamp).
- One section per table, with the Kaggle label as subtitle and the business purpose.
- A per-table column table: name, business description, LogicalType + PostgreSQL
  type, nullable, key, allowed values, min/max, unique count, DQ notes.
- A relationships block documenting the three cross-table links.

Reference: specs/001-data-genai-platform-baseline/contracts/dictionary.md
"""

from __future__ import annotations

from src.contracts.dictionary import (
    DataDictionaryDocument,
    DictionaryEntry,
    RelationshipEntry,
    TableDictionary,
)


def _format_entry_row(entry: DictionaryEntry) -> str:
    """Render one DictionaryEntry as a table row."""
    # Type column: show the PostgreSQL display type (which carries precision);
    # note the engine-neutral LogicalType in parentheses for clarity.
    type_str = f"{entry.postgres_type} ({entry.logical_type.value})"
    # Key column: primary/foreign/—
    key_str = entry.key_kind if entry.key_kind else ("key" if entry.is_key else "—")
    # Allowed values: list the seeds if present.
    allowed = ", ".join(entry.allowed_values) if entry.allowed_values else "—"
    # Min/Max for numeric/date columns.
    min_max = "—"
    if entry.min_value and entry.max_value:
        min_max = f"{entry.min_value} … {entry.max_value}"
    elif entry.min_value:
        min_max = f"min {entry.min_value}"
    elif entry.max_value:
        min_max = f"max {entry.max_value}"
    # DQ notes: join with " | ".
    dq = " | ".join(entry.data_quality_notes) if entry.data_quality_notes else "—"
    # Nullability
    null_str = "NULL" if entry.nullable else "NOT NULL"
    # Escape pipes in free-text fields so they don't break the markdown table.
    def _esc(s: str) -> str:
        return s.replace("|", "\\|")

    return (
        f"| `{_esc(entry.name)}` | {_esc(entry.business_description)} "
        f"| {_esc(type_str)} | {null_str} | {key_str} | {_esc(allowed)} "
        f"| {_esc(min_max)} | {entry.unique_count} | {_esc(dq)} |"
    )


def _render_table(table: TableDictionary) -> str:
    """Render one TableDictionary section."""
    lines: list[str] = []
    lines.append(f"## `{table.name}` — {table.kaggle_label}")
    lines.append("")
    lines.append(f"**Purpose**: {table.purpose}")
    lines.append("")
    lines.append(f"**Primary key**: {', '.join(table.primary_key) if table.primary_key else '(none)'}")
    lines.append("")
    # Column table
    headers = (
        "| Column | Business description | Type | Null | Key | "
        "Allowed values | Min/Max | Unique | Data-quality notes |"
    )
    sep = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    lines.append(headers)
    lines.append(sep)
    for entry in table.columns:
        lines.append(_format_entry_row(entry))
    lines.append("")
    # Relationships
    lines.append("**Relationships**:")
    if table.relationships:
        for rel in table.relationships:
            lines.append(_render_relationship(rel))
    else:
        lines.append("- _(none — this table is a root in the schema graph)_")
    lines.append("")
    return "\n".join(lines)


def _render_relationship(rel: RelationshipEntry) -> str:
    """Render one RelationshipEntry as a bullet."""
    return (
        f"- `{rel.from_column}` → `{rel.to_table}.{rel.to_column}` "
        f"({rel.cardinality})"
    )


def render_markdown(document: DataDictionaryDocument) -> str:
    """Render a `DataDictionaryDocument` to a Markdown string."""
    lines: list[str] = []
    lines.append("# Data Dictionary — Plataforma de Datos y GenAI")
    lines.append("")
    lines.append(f"- **Source file**: `{document.source_file}`")
    lines.append(f"- **Source SHA-256**: `{document.source_sha256}`")
    lines.append(f"- **Generated at**: {document.generated_at.isoformat()}")
    lines.append(
        "- **Tables**: "
        + ", ".join(f"`{t.name}` ({t.kaggle_label})" for t in document.tables)
    )
    lines.append("")
    lines.append(
        "> This dictionary integrates Kaggle semantic descriptions with "
        "EDA-derived database types. Regeneratable via `uv run python -m "
        "src.cli.main generate-dictionary` (FR-012)."
    )
    lines.append("")
    # One section per table.
    for table in document.tables:
        lines.append(_render_table(table))
    # Cross-table relationships overview at the end.
    lines.append("---")
    lines.append("")
    lines.append("## Cross-Table Relationships (overview)")
    lines.append("")
    lines.append("- **`Returns.Order ID` → `Orders.Order ID`** (N:1) — a return refers to an order.")
    lines.append("- **`People.Region` → `Orders.Region`** (1:N) — regional sales-person governs orders.")
    lines.append("- **`People.Region` → `Returns.Region`** (1:N) — regional governance applies to returns.")
    lines.append("")
    lines.append(
        "> Row-Level Security on `People`/`Region` is **v2.0 scope** and is NOT "
        "enforced at this baseline. The data model preserves the `Region` "
        "columns needed for it."
    )
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_markdown"]
