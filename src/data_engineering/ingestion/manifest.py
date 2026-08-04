"""Load-artifact provenance writer (minimal MLOps footprint).

Emits a `LoadArtifactManifest` (JSON) capturing per-load provenance so the
warehouse is traceable to its source and code commit, WITHOUT model-tracking
infra (research.md Part B: "Adopt NOW — minimal, honest, matches the
no-models-yet reality"). The validator consumes it (FR-014).

Reference: specs/001-data-genai-platform-baseline/research.md Part B
            specs/001-data-genai-platform-baseline/contracts/ingestion.md
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pandas

from src.contracts.data_access import LoadResult
from src.contracts.ingestion import (
    LoadArtifactManifest,
    SchemaInferenceResult,
    TableLoadSummary,
)


SCHEMA_VERSION = "v1"


def _sha256_of_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    """Return the current git commit SHA, or 'unknown' if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _default_tool_versions() -> dict[str, str]:
    """Capture versions for libraries THIS module legitimately imports.

    NOTE: psycopg is NOT imported here (it lives only in the postgres
    adapter, per constitution Principle III). Callers may inject additional
    versions via `build_manifest(extra_tool_versions=...)`.
    """
    return {
        "python": platform.python_version(),
        "pandas": str(pandas.__version__),
        "platform": platform.platform(),
    }


def build_manifest(
    schema_inference: SchemaInferenceResult,
    load_results: dict[str, LoadResult],
    extra_tool_versions: dict[str, str] | None = None,
) -> LoadArtifactManifest:
    """Build a `LoadArtifactManifest` from the EDA result and load outcomes.

    Reconciles each table's loaded row count against the EDA-inferred row
    count (FR-015): a mismatch is recorded as an error in the table's
    `TableLoadSummary.load_result.errors`.

    `extra_tool_versions` lets callers that legitimately import engine-specific
    libraries (e.g., the postgres adapter importing psycopg) contribute their
    versions to the manifest WITHOUT this module importing them (constitution
    Principle III: psycopg confined to `src/data_access/adapters/postgres/`).
    """
    # Map table name -> EDA profile row count for reconciliation.
    eda_row_counts: dict[str, int] = {
        t.sheet_name: t.row_count for t in schema_inference.tables
    }

    per_table: list[TableLoadSummary] = []
    for table_name, lr in load_results.items():
        expected = eda_row_counts.get(table_name)
        errors: list[str] = list(lr.errors)
        if expected is not None and lr.rows_loaded != expected:
            errors.append(
                f"Row-count mismatch: loaded {lr.rows_loaded} but EDA inferred {expected}"
            )
        per_table.append(
            TableLoadSummary(
                table_name=table_name,
                row_count=lr.rows_loaded,
                column_count=0,  # populated by the validator from information_schema if needed
                load_result=LoadResult(
                    table_name=lr.table_name,
                    rows_loaded=lr.rows_loaded,
                    rows_rejected=lr.rows_rejected,
                    errors=errors,
                ),
            )
        )

    tool_versions = _default_tool_versions()
    if extra_tool_versions:
        tool_versions.update(extra_tool_versions)
    return LoadArtifactManifest(
        source_file=schema_inference.source_file,
        source_sha256=schema_inference.source_sha256,
        schema_version=SCHEMA_VERSION,
        loaded_at=datetime.now(timezone.utc),
        git_commit=_git_commit(),
        tool_versions=tool_versions,
        per_table=per_table,
    )


def _default_serializer(obj: Any) -> str:
    """JSON serializer for types Pydantic doesn't natively dump (e.g., datetime)."""
    if hasattr(obj, "isoformat") and callable(getattr(obj, "isoformat")):
        return cast(str, obj.isoformat())
    return str(obj)


def write_manifest(manifest: LoadArtifactManifest, output_path: str | Path) -> Path:
    """Serialize a `LoadArtifactManifest` to a JSON file.

    Returns the path written. The validator reads this file to verify
    provenance (FR-014: source_sha256 matches the .xlsx, rows_loaded matches
    EDA counts).
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(manifest.model_dump_json())
    serialized: str = json.dumps(data, indent=2, default=_default_serializer)
    path.write_text(serialized)
    return path


def sha256_of_file(path: str | Path) -> str:
    """Public alias for tests/callers that need the source-file hash."""
    return _sha256_of_file(Path(path))


__all__ = [
    "SCHEMA_VERSION",
    "build_manifest",
    "write_manifest",
    "sha256_of_file",
]
