"""Semantic Layer renderer (v2.0) — serializes the document to two artifacts.

- `render_json(document) -> str` : canonical, deterministic JSON (no
  `generated_at`, no `viewers`) — for git-diffability and SC-005 determinism.
- `render_markdown(document) -> str` : human-readable Markdown with the
  timestamp, hashes, assumptions, full formulas, relationships, DQ notes.

Pure functions: no file I/O (the caller writes the output to disk).

Reference: specs/003-semantic-layer-v1/research.md Part D (Determinism)
            specs/003-semantic-layer-v1/data-model.md § Serialization notes
            specs/003-semantic-layer-v1/tasks.md T011
"""

from __future__ import annotations

import json

from src.contracts.semantic_layer import SemanticLayerDocument


# Fields excluded from the canonical JSON artifact for determinism (FR-007 / SC-005):
#  - `generated_at` : contains a timestamp — would break byte-determinism
#                     across runs with the same inputs.
_JSON_EXCLUDE_FIELDS: set[str] = {"generated_at"}


def render_json(document: SemanticLayerDocument) -> str:
    """Render a canonical, deterministic JSON string.

    The JSON excludes `generated_at` (timestamp — non-deterministic) and
    uses `sort_keys=True` + `exclude_none=True` + `ensure_ascii=False` so
    that two builds from the same inputs produce byte-identical JSON.
    """
    payload = document.model_dump(
        exclude=_JSON_EXCLUDE_FIELDS,
        exclude_none=True,
    )
    return json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    )


def render_markdown(document: SemanticLayerDocument) -> str:
    """Render a human-readable Markdown description of the Semantic Layer.

    Includes provenance (source + semantic_source hashes), generated_at,
    assumptions, tables classification, the 8 metrics with their formulas,
    the 11 dimensions grouped by type, and the 2 cross-table relationships.
    """
    lines: list[str] = []
    lines.append("# Semantic Layer — Plataforma de Datos y GenAI")
    lines.append("")
    lines.append(f"- **Version**: `{document.version}`")
    lines.append(f"- **Generated at**: `{document.generated_at.isoformat()}`")
    lines.append(f"- **Source SHA-256** (load manifest provenance): `{document.source_sha256}`")
    lines.append(
        f"- **Semantic source SHA-256** (`semantic_source.py` provenance): "
        f"`{document.semantic_source_sha256}`"
    )
    lines.append("")
    lines.append(
        "> Regeneratable via `uv run python -m src.cli.main generate-semantic-layer` (FR-008). "
        "The JSON artifact is deterministic (no `generated_at` field); this Markdown "
        "is for human reading."
    )
    lines.append("")

    # --- Assumptions ---
    if document.assumptions:
        lines.append("## Assumptions")
        lines.append("")
        for assumption in document.assumptions:
            lines.append(f"- {assumption}")
        lines.append("")

    # --- Tables ---
    lines.append("## Tables")
    lines.append("")
    lines.append("| Table | Type | Purpose |")
    lines.append("| --- | --- | --- |")
    for t in document.tables:
        lines.append(f"| `{t.name}` | {t.table_type} | {t.purpose} |")
    lines.append("")

    # --- Metrics ---
    lines.append("## Metrics")
    lines.append("")
    for m in document.metrics:
        lines.append(f"### `{m.name}`")
        lines.append("")
        lines.append(f"- **Business description**: {m.business_description}")
        lines.append(f"- **Aggregation**: `{m.aggregation}`")
        lines.append(f"- **Source table**: `{m.source_table}`")
        lines.append(f"- **Uses Returns**: {m.uses_returns}")
        if m.derives_from:
            lines.append(f"- **Derives from**: {', '.join(m.derives_from)}")
        if m.assumption:
            lines.append(f"- **Assumption**: {m.assumption}")
        lines.append("- **Formula (SQL)**:")
        lines.append("")
        lines.append("```sql")
        lines.append(m.formula_sql)
        lines.append("```")
        lines.append("")

    # --- Dimensions ---
    lines.append("## Dimensions")
    lines.append("")
    # Group by dimension_type for readability.
    by_type: dict[str, list[str]] = {"geographic": [], "categorical": [], "temporal": []}
    for d in document.dimensions:
        by_type.setdefault(d.dimension_type, []).append(
            f"`{d.name}` (`{d.column}`) — {d.business_description}"
        )
    for d_type, items in by_type.items():
        if not items:
            continue
        lines.append(f"### {d_type.capitalize()}")
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    # --- Relationships ---
    lines.append("## Relationships")
    lines.append("")
    for r in document.relationships:
        lines.append(
            f"### `{r.name}`"
        )
        lines.append("")
        lines.append(
            f"- `{r.from_table}.{r.from_column}` → `{r.to_table}.{r.to_column}` "
            f"({r.cardinality}, {r.join_type} JOIN)"
        )
        if r.notes:
            lines.append(f"- **Notes**: {r.notes}")
        lines.append("")

    return "\n".join(lines)


__all__ = ["render_json", "render_markdown"]
